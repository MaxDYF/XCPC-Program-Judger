# Security policy

Please report a security issue privately through GitHub's repository security advisory feature when available. Avoid posting exploitable details or private user content in a public issue.

The public web service has no user authentication. Completed evaluation reports are visible in the history UI. Deploy behind a loopback-bound server and a controlled tunnel, keep dependencies patched, and keep the API key and history database outside version control. The in-process rate limit does not coordinate across replicas.
