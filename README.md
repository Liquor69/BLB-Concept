# BLB Concept landings

This FastAPI application serves the landing pages and owns the Gmail OAuth lead
channel at `POST /integrations/gmail/lead`.

## Local development

Start the local server:

```powershell
.\scripts\start-local.ps1
```

Install dependencies, then start it:

```powershell
.\scripts\start-local.ps1 -Install
```

The tattoo-removal landing is then available at
`http://127.0.0.1:8010/landings/tattoo-removal/`.

## Gmail setup

Set the variables in `.env.example` in the deployment environment. Register
`GMAIL_REDIRECT_URI` in the Google OAuth client, then open:

```text
https://<public-host>/integrations/gmail/connect?connect_token=<GMAIL_CONNECT_TOKEN>
```

Authorize the shared sender account with `gmail.send`. Landing `question` and
`claim` submissions are then delivered to `LEAD_RECIPIENTS_CSV`.

Set `GMAIL_STATE_DB` to a Railway Volume path (for example,
`/data/gmail-state.sqlite3`) so the connected account survives deployments.
