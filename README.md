# BLB Concept landings

This FastAPI application serves the landing pages and owns the Gmail OAuth lead
channel at `POST /integrations/gmail/lead`.

## Gmail setup

Set the variables in `.env.example` in the deployment environment. Register
`GMAIL_REDIRECT_URI` in the Google OAuth client, then open:

```text
https://<public-host>/integrations/gmail/connect?connect_token=<GMAIL_CONNECT_TOKEN>
```

Authorize the shared sender account with `gmail.send`. Landing `question` and
`claim` submissions are then delivered to `LEAD_RECIPIENTS_CSV`.
