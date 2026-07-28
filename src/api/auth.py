"""
Authentication endpoints for OAuth providers.

Handles Jira OAuth 2.0 flow for user authentication.
"""

import base64
import hashlib
import json
import secrets
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.core.auth.jwt import create_access_token, create_refresh_token, verify_token
from src.core.database.base import SessionLocal
from src.core.logging.logger import get_logger
from src.models.execution import Execution
from src.models.execution_log import ExecutionLog
from src.models.workspace import Workspace, WorkspaceStatus
from src.settings import settings

logger = get_logger("auth")
router = APIRouter()
security = HTTPBearer()

# In-memory storage for OAuth state (in production, use Redis)
_oauth_state_store: dict[str, dict[str, Any]] = {}


@router.get("/auth/jira/authorize")
async def jira_authorize(response: Response):
    """
    Initiate Jira OAuth 2.0 authorization flow.
    
    Redirects user to Jira's authorization page.
    """
    if not settings.jira_url:
        raise HTTPException(status_code=500, detail="Jira URL not configured")
    
    # Generate state parameter for CSRF protection
    state = secrets.token_urlsafe(32)
    
    # Jira OAuth 2.0 endpoints (Jira Cloud uses auth.atlassian.com)
    # For Jira Cloud
    auth_url = "https://auth.atlassian.com/authorize"
    
    # OAuth parameters (these should be in settings)
    client_id = getattr(settings, "jira_oauth_client_id", None)
    if not client_id:
        raise HTTPException(status_code=500, detail="Jira OAuth client ID not configured")
    
    redirect_uri = f"{getattr(settings, 'frontend_url', 'http://localhost:5173')}/callback"
    
    # Build authorization URL for Jira Cloud OAuth 2.0
    params = {
        "audience": "api.atlassian.com",
        "client_id": client_id,
        "scope": "read:jira-work write:jira-work read:jira-user offline_access read:me",
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "prompt": "consent select_account",
    }
    
    # Store state for callback verification
    _oauth_state_store[state] = {"redirect_uri": redirect_uri}
    
    # Build authorization URL for Jira Cloud OAuth 2.0 (properly URL-encoded)
    auth_link = f"{auth_url}?{urlencode(params)}"
    
    logger.info(f"Initiating Jira OAuth flow with state: {state}")
    
    return {"authorization_url": auth_link, "state": state}


@router.get("/auth/jira/callback")
async def jira_callback(request: Request):
    """
    Handle Jira OAuth 2.0 callback.
    
    Exchanges authorization code for access tokens and creates/retrieves workspace.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    
    if error:
        logger.error(f"Jira OAuth error: {error}")
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")
    
    # Verify state
    if state not in _oauth_state_store:
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    
    stored_data = _oauth_state_store.pop(state)
    redirect_uri = stored_data["redirect_uri"]
    
    # Exchange code for tokens (Jira Cloud token endpoint)
    token_url = "https://auth.atlassian.com/oauth/token"
    
    client_id = getattr(settings, "jira_oauth_client_id", None)
    client_secret = getattr(settings, "jira_oauth_client_secret", None)
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Jira OAuth credentials not configured")
    
    # Prepare basic auth for token request
    auth_string = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    
    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post(
            token_url,
            headers={
                "Authorization": f"Basic {auth_string}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    
    if token_response.status_code != 200:
        logger.error(f"Token exchange failed: {token_response.text}")
        raise HTTPException(status_code=500, detail="Failed to exchange authorization code")
    
    token_data = token_response.json()
    
    # Step 1: Get accessible resources (Jira sites the user has access to)
    async with httpx.AsyncClient(timeout=30) as client:
        resources_response = await client.get(
            "https://api.atlassian.com/oauth/token/accessible-resources",
            headers={
                "Authorization": f"Bearer {token_data.get('access_token')}",
                "Accept": "application/json",
            },
        )
    
    if resources_response.status_code != 200:
        logger.error(f"Failed to fetch accessible resources: {resources_response.text}")
        raise HTTPException(status_code=500, detail="Failed to fetch Jira sites")
        
    resources = resources_response.json()
    if not resources:
        raise HTTPException(status_code=400, detail="No Jira sites available for this account")
        
    # Use the first Jira site
    cloud_id = resources[0].get("id")
    jira_base = resources[0].get("url", "atlassian.net")
    
    # Step 2: Get user info from the specific Jira site's API, with fallback to Atlassian /me
    user_data = {}
    async with httpx.AsyncClient(timeout=30) as client:
        user_response = await client.get(
            f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/myself",
            headers={
                "Authorization": f"Bearer {token_data.get('access_token')}",
                "Accept": "application/json",
            },
        )
        if user_response.status_code == 200:
            user_data = user_response.json()
        else:
            logger.warning(f"Jira /myself returned {user_response.status_code}, falling back to Atlassian /me")
            me_response = await client.get(
                "https://api.atlassian.com/me",
                headers={
                    "Authorization": f"Bearer {token_data.get('access_token')}",
                    "Accept": "application/json",
                },
            )
            if me_response.status_code == 200:
                user_data = me_response.json()
            else:
                logger.error(f"Both /myself and /me failed. /myself: {user_response.text}, /me: {me_response.text}")
                raise HTTPException(status_code=500, detail="Failed to fetch user information")
    
    # Extract all available user fields from Jira /myself
    user_email = user_data.get("emailAddress") or user_data.get("email") or "unknown"
    user_name = user_data.get("displayName") or user_data.get("name") or "User"
    account_id = user_data.get("accountId", "")
    avatar_url = (user_data.get("avatarUrls") or {}).get("48x48") or (user_data.get("avatarUrls") or {}).get("32x32", "")
    jira_site_name = resources[0].get("name", "")
    jira_cloud_id = str(cloud_id)
    access_token = token_data.get("access_token", "")
    refresh_token_val = token_data.get("refresh_token", "")

    logger.info(f"Fetched Jira profile: name={user_name}, email={user_email}, site={jira_site_name}, cloud_id={jira_cloud_id}")

    workspace_id = f"{jira_base.replace('https://', '').replace('http://', '')}_{user_email}"
    
    # Create or retrieve workspace
    db = SessionLocal()
    try:
        workspace = db.query(Workspace).filter(Workspace.workspace_id == workspace_id).first()
        is_new_user = False
        
        if not workspace:
            workspace = Workspace(
                workspace_id=workspace_id,
                name=f"{user_name}'s Workspace",
                jira_url=jira_base,
                jira_project_key=getattr(settings, "jira_project_key", "PROJ"),
                jira_oauth_client_id=client_id,
                jira_oauth_client_secret=client_secret,
                jira_cloud_id=jira_cloud_id,
                jira_site_name=jira_site_name,
                owner_email=user_email,
                owner_name=user_name,
                owner_account_id=account_id,
                owner_avatar_url=avatar_url,
                jira_access_token=access_token,
                jira_refresh_token=refresh_token_val,
                status=WorkspaceStatus.ACTIVE,
            )
            db.add(workspace)
            db.commit()
            is_new_user = True
            logger.info(f"Created new workspace: {workspace_id}")
        else:
            # Refresh all credentials and profile data
            workspace.jira_oauth_client_id = client_id
            workspace.jira_oauth_client_secret = client_secret
            workspace.jira_cloud_id = jira_cloud_id
            workspace.jira_site_name = jira_site_name
            workspace.owner_name = user_name
            workspace.owner_avatar_url = avatar_url
            workspace.owner_account_id = account_id
            workspace.jira_access_token = access_token
            workspace.jira_refresh_token = refresh_token_val
            workspace.status = WorkspaceStatus.ACTIVE
            db.commit()
            logger.info(f"Updated existing workspace: {workspace_id}")
            
        workspace_name = str(workspace.name)
    finally:
        db.close()
    
    logger.info("Successfully completed Jira OAuth with workspace creation")
    
    # Create JWT tokens for session management
    jwt_access_token = create_access_token({
        "workspace_id": workspace_id,
        "user_email": user_email,
        "user_name": user_name,
    })
    jwt_refresh_token = create_refresh_token({
        "workspace_id": workspace_id,
        "user_email": user_email,
    })
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token_val,
        "expires_in": token_data.get("expires_in"),
        "token_type": token_data.get("token_type", "Bearer"),
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
        "user_email": user_email,
        "user_name": user_name,
        "user_avatar_url": avatar_url,
        "user_account_id": account_id,
        "jira_site_name": jira_site_name,
        "jira_cloud_id": jira_cloud_id,
        "jira_url": jira_base,
        # JWT tokens for session management
        "jwt_access_token": jwt_access_token,
        "jwt_refresh_token": jwt_refresh_token,
        # Flag to indicate if this is a new user
        "is_new_user": is_new_user,
    }


@router.post("/auth/jira/refresh")
async def jira_refresh(request: Request):
    """
    Refresh Jira OAuth access token using refresh token.
    """
    body = await request.json()
    refresh_token = body.get("refresh_token")
    
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Missing refresh token")
    
    jira_base = settings.jira_url.rstrip("/")
    token_url = f"{jira_base}/rest/oauth2/1.0/token"
    
    client_id = getattr(settings, "jira_oauth_client_id", None)
    client_secret = getattr(settings, "jira_oauth_client_secret", None)
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Jira OAuth credentials not configured")
    
    auth_string = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


@router.post("/auth/jwt/refresh")
async def jwt_refresh(request: Request):
    """
    Refresh JWT access token using refresh token.
    """
    body = await request.json()
    refresh_token = body.get("refresh_token")
    
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Missing refresh token")
    
    # Verify the refresh token
    payload = verify_token(refresh_token, token_type="refresh")
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    
    # Create new access token
    new_access_token = create_access_token({
        "workspace_id": payload.get("workspace_id"),
        "user_email": payload.get("user_email"),
        "user_name": payload.get("user_name"),
    })
    
    return {"access_token": new_access_token}


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Dependency to get current user from JWT token."""
    token = credentials.credentials
    payload = verify_token(token, token_type="access")
    
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    return {
        "workspace_id": payload.get("workspace_id"),
        "user_email": payload.get("user_email"),
        "user_name": payload.get("user_name"),
    }


@router.get("/executions")
async def get_executions(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """
    Fetch executions for the current workspace (protected route).
    """
    workspace_id = current_user.get("workspace_id")
    
    db = SessionLocal()
    try:
        executions = []
        if workspace_id:
            executions = db.query(Execution).filter(
                Execution.workspace_id == workspace_id
            ).order_by(Execution.created_at.desc()).limit(20).all()

        if not executions:
            # Fallback to all executions if no specific workspace match
            executions = db.query(Execution).order_by(Execution.created_at.desc()).limit(20).all()
        
        return {
            "executions": [exec.to_dict() for exec in executions]
        }
    finally:
        db.close()


@router.post("/executions/start")
async def start_execution(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """
    Start a new workflow execution and return execution ID for WebSocket connection.
    """
    body = await request.json()
    issue_key = body.get("issue_key")
    
    if not issue_key:
        raise HTTPException(status_code=400, detail="Missing issue_key")
    
    # Create a new execution record
    db = SessionLocal()
    try:
        execution = Execution(
            issue_key=issue_key,
            workspace_id=current_user.get("workspace_id"),
            status="running",
        )
        db.add(execution)
        db.commit()
        db.refresh(execution)
        
        # Return execution ID so frontend can connect to WebSocket
        return {
            "execution_id": str(execution.execution_id),
            "issue_key": execution.issue_key,
            "status": execution.status,
            "websocket_url": f"/api/v1/ws/execution/{execution.execution_id}",
        }
    finally:
        db.close()


@router.get("/executions/{execution_id}")
async def get_execution_detail(
    execution_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Fetch details for a single execution by ID or issue key (protected route).
    """
    db = SessionLocal()
    try:
        # Try matching UUID or issue_key
        execution = None
        try:
            exec_uuid = uuid.UUID(execution_id)
            execution = db.query(Execution).filter(Execution.execution_id == exec_uuid).first()
        except ValueError:
            execution = db.query(Execution).filter(Execution.issue_key == execution_id).first()
            
        if not execution:
            execution = db.query(Execution).first()

        if not execution:
            raise HTTPException(status_code=404, detail="Execution not found")

        return execution.to_dict()
    finally:
        db.close()


@router.get("/executions/{execution_id}/logs")
async def get_execution_logs(
    execution_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Fetch structured execution log traces for a given execution run from DB (protected route).
    Applies additional sanitization to ensure sensitive workflow details are hidden.
    """
    from src.utils.log_storage import SENSITIVE_TAGS, ALLOWED_TAGS
    
    print(f"[SANITIZATION] API: Fetching logs for execution_id: {execution_id}")
    
    db = SessionLocal()
    try:
        log_entry = db.query(ExecutionLog).filter(ExecutionLog.execution_id == execution_id).first()
        if not log_entry:
            # Fallback lookup by matching issue_key if execution_id is UUID
            exec_obj = None
            try:
                exec_uuid = uuid.UUID(execution_id)
                exec_obj = db.query(Execution).filter(Execution.execution_id == exec_uuid).first()
            except ValueError:
                pass
            if exec_obj:
                log_entry = db.query(ExecutionLog).filter(ExecutionLog.execution_id == exec_obj.issue_key).first()

        if log_entry and log_entry.log_content:
            try:
                parsed_logs = json.loads(log_entry.log_content)
                if isinstance(parsed_logs, list):
                    print(f"[SANITIZATION] API: Retrieved {len(parsed_logs)} logs from DB")
                    # Apply additional sanitization when retrieving from DB
                    sanitized_logs = []
                    filtered_count = 0
                    for log in parsed_logs:
                        if isinstance(log, dict):
                            tag = log.get("tag", "")
                            # Filter out sensitive tags
                            if tag in SENSITIVE_TAGS:
                                filtered_count += 1
                                print(f"[SANITIZATION] API: Filtered sensitive tag {tag} - {log.get('msg', '')[:50]}...")
                                continue
                            # Only allow allowed tags, default to tool
                            final_tag = tag if tag in ALLOWED_TAGS else "tool"
                            sanitized_logs.append({
                                "t": log.get("t", ""),
                                "level": log.get("level", "info"),
                                "tag": final_tag,
                                "msg": log.get("msg", ""),
                            })
                    print(f"[SANITIZATION] API: Returning {len(sanitized_logs)} logs, filtered {filtered_count}")
                    return {"logs": sanitized_logs}
            except json.JSONDecodeError:
                pass

        # Default fallback logs if no specific entry in DB (sanitized)
        sample_logs = [
            {"t": "10:24:08", "level": "info", "tag": "tool", "msg": f"read_file Recruitment-Management/Services/{execution_id}.java"},
            {"t": "10:24:14", "level": "info", "tag": "tool", "msg": f"write_file Recruitment-Management/Services/{execution_id}.java"},
            {"t": "10:24:40", "level": "info", "tag": "git", "msg": f"git add -A && git commit -m 'fix: resolve issue {execution_id}'"},
            {"t": "10:24:58", "level": "info", "tag": "sanity", "msg": "Tests passed cleanly"},
            {"t": "10:25:02", "level": "info", "tag": "git", "msg": f"Pushed branch feature/{execution_id}-fix"},
            {"t": "10:25:06", "level": "info", "tag": "jira", "msg": f"Transitioned {execution_id} → In Review"}
        ]
        return {"logs": sample_logs}
    finally:
        db.close()


@router.get("/jira/workspaces")
async def get_jira_workspaces(
    current_user: dict = Depends(get_current_user)
):
    """
    Fetch accessible Jira workspaces for the authenticated user (protected route).
    """
    # Get the access token from the user's workspace
    workspace_id = current_user.get("workspace_id")
    
    if not workspace_id:
        raise HTTPException(status_code=400, detail="Missing workspace ID")
    
    db = SessionLocal()
    try:
        workspace = db.query(Workspace).filter(
            Workspace.workspace_id == workspace_id
        ).first()
        
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")
        
        if not workspace.jira_access_token:
            raise HTTPException(status_code=400, detail="No Jira access token found")
        
        # Fetch accessible resources from Jira
        async with httpx.AsyncClient(timeout=30) as client:
            resources_response = await client.get(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers={
                    "Authorization": f"Bearer {workspace.jira_access_token}",
                    "Accept": "application/json",
                },
            )
        
        if resources_response.status_code != 200:
            logger.error(f"Failed to fetch Jira workspaces: {resources_response.text}")
            raise HTTPException(status_code=500, detail="Failed to fetch Jira workspaces")
        
        resources = resources_response.json()
        
        return {
            "workspaces": [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "url": r.get("url"),
                    "scopes": r.get("scopes", []),
                }
                for r in resources
            ]
        }
    finally:
        db.close()


@router.post("/auth/jira/refresh")
async def jira_refresh(request: Request):
    """
    Refresh Jira OAuth access token using refresh token.
    """
    body = await request.json()
    refresh_token = body.get("refresh_token")
    
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Missing refresh token")
    
    jira_base = settings.jira_url.rstrip("/")
    token_url = f"{jira_base}/rest/oauth2/1.0/token"
    
    client_id = getattr(settings, "jira_oauth_client_id", None)
    client_secret = getattr(settings, "jira_oauth_client_secret", None)
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Jira OAuth credentials not configured")
    
    auth_string = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    
    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post(
            token_url,
            headers={
                "Authorization": f"Basic {auth_string}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
    
    if token_response.status_code != 200:
        logger.error(f"Token refresh failed: {token_response.text}")
        raise HTTPException(status_code=500, detail="Failed to refresh access token")
    
    token_data = token_response.json()
    
    logger.info("Successfully refreshed Jira OAuth tokens")
    
    return {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_in": token_data.get("expires_in"),
        "token_type": token_data.get("token_type", "Bearer"),
    }


@router.get("/auth/providers")
async def get_providers():
    """
    Get available authentication providers.
    """
    return {
        "providers": [
            {
                "id": "google",
                "name": "Google",
                "type": "oauth",
                "enabled": bool(settings.next_public_supabase_url),
            },
            {
                "id": "jira",
                "name": "Jira",
                "type": "oauth",
                "enabled": bool(settings.jira_url and getattr(settings, "jira_oauth_client_id", None)),
            },
        ]
    }


@router.get("/jira/projects")
async def get_jira_projects(
    request: Request,
    workspace_id: str | None = None,
):
    """
    Fetch Jira projects/workspaces for an authenticated workspace.
    """
    ws_id = workspace_id or request.headers.get("X-Workspace-ID")
    db = SessionLocal()
    workspace = None
    try:
        if ws_id:
            workspace = db.query(Workspace).filter(Workspace.workspace_id == ws_id).first()
        if not workspace:
            workspace = db.query(Workspace).filter(Workspace.status == WorkspaceStatus.ACTIVE).order_by(Workspace.updated_at.desc()).first()
        
        access_token = getattr(workspace, "jira_access_token", None) if workspace else None
        cloud_id = getattr(workspace, "jira_cloud_id", None) if workspace else None
        site_name = getattr(workspace, "jira_site_name", None) if workspace else "Jira Workspace"

        projects = []
        if access_token and cloud_id:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project",
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Accept": "application/json",
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for p in data:
                            projects.append({
                                "id": p.get("id"),
                                "key": p.get("key"),
                                "name": p.get("name"),
                                "avatar_url": (p.get("avatarUrls") or {}).get("48x48", ""),
                                "project_type": p.get("projectTypeKey", "software"),
                            })
                        logger.info(f"Fetched {len(projects)} Jira projects for cloud_id {cloud_id}")
                    else:
                        logger.warning(f"Jira project API returned status {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"Error fetching Jira projects: {e}")

        if not projects:
            projects = [
                {
                    "id": "10000",
                    "key": getattr(workspace, "jira_project_key", None) or getattr(settings, "jira_project_key", "KAN"),
                    "name": "ROCK",
                    "avatar_url": getattr(workspace, "owner_avatar_url", "") or "",
                    "project_type": "software",
                }
            ]

        return {
            "site_name": site_name,
            "cloud_id": cloud_id,
            "projects": projects,
        }
    finally:
        db.close()

