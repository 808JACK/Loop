import asyncio
import httpx
from src.integrations.jira.jira_client import get_jira_client

async def create_issue():
    client = get_jira_client()
    
    issue_data = {
        "project_key": "KAN",
        "summary": "Logout endpoint does not destroy session properly",
        "description": """The logout endpoint only nullifies the user object in the session but does not destroy the session itself. This allows session reuse after logout.

Repo: https://github.com/808JACK/Recruitment-Management.git#issue
Requested reviewers: 808sarthak

Acceptance criteria:
- session.destroy() is called on logout
- Server-side session store is invalidated
- Reuse of destroyed session cookie returns 401
- Secure cookie flags are verified""",
        "issue_type": "Task",
        "labels": ["ai-ready"],
        "priority": "High"
    }
    
    try:
        result = await client.create_issue(**issue_data)
        print(f"Issue created: {result['key']}")
        print(f"URL: {client.base_url}/browse/{result['key']}")
        return result
    except httpx.HTTPStatusError as e:
        print(f"Error creating issue: {e}")
        print(f"Response: {e.response.text}")
        return None

if __name__ == "__main__":
    asyncio.run(create_issue())
