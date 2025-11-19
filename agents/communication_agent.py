"""Communication agent responsible for sending approval emails."""

import asyncio
import base64
import logging
import os
import traceback
from email.mime.text import MIMEText
from textwrap import dedent
from typing import Dict, List, Optional

import google.auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class CommunicationAgent:
    """Thin wrapper around the Gmail API for approval workflows."""

    GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"

    def __init__(self, sender_email: Optional[str], approval_callback_url: Optional[str] = None):
        self.sender_email = sender_email
        self.approval_callback_url = approval_callback_url
        self._gmail_service = None
        self._init_error = None

        if not sender_email:
            logging.warning("COMM AGENT: sender email not configured; emails will be logged only.")
            return

        try:
            # --- AUTHENTICATION STRATEGY ---
            # Look for "token.json" (Personal Gmail / OAuth User flow)
            # This is required because Service Accounts cannot impersonate personal @gmail.com users.
            token_path = os.environ.get("GMAIL_TOKEN_PATH", "token.json")
            
            if os.path.exists(token_path):
                logging.info(f"COMM AGENT: Found personal Gmail token at {token_path}. Using OAuth User Credentials.")
                credentials = Credentials.from_authorized_user_file(token_path, scopes=[self.GMAIL_SCOPE])
            else:
                # Priority 2: Fallback to Default credentials (Service Account / Enterprise flow)
                logging.info("COMM AGENT: No token.json found. Attempting Default Credentials (Service Account).")
                credentials, _ = google.auth.default(scopes=[self.GMAIL_SCOPE])

            self._gmail_service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
            logging.info("COMM AGENT: Gmail client initialized.")
        except Exception as exc:
            self._init_error = f"{str(exc)}\n{traceback.format_exc()}"
            logging.error(f"COMM AGENT: Failed to initialize Gmail API client: {exc}")
            self._gmail_service = None

    async def send_approval_email(
        self,
        *,
        session_id: str,
        requester_email: str,
        requested_role: str,
        project_scope: str,
        approvers: List[str],
        justification: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Sends (or simulates) an approval email. Returns metadata suitable for audit trails.
        """
        if not approvers:
            raise ValueError("At least one approver is required to send an approval email.")

        justification_text = justification or "No justification supplied."
        subject = f"[ACTION REQUIRED] Access request for {requester_email}"
        body = dedent(
            f"""
            Hello,

            {requester_email} has requested the IAM role '{requested_role}' for project '{project_scope}'.
            Session ID: {session_id}
            Justification: {justification_text}

            Reply to this email with APPROVED or DENIED (optionally include details).

            If you prefer the console, use the callback URL:
            {self.approval_callback_url or 'No callback URL configured'}

            Thank you,
            Zero-Touch IAM Orchestrator
            """
        ).strip()

        payload = {
            "subject": subject,
            "body": body,
            "approvers": approvers,
            "session_id": session_id,
        }

        if not self.sender_email or not self._gmail_service:
            # Determine exactly WHY we are simulating
            if not self.sender_email:
                reason = "SENDER_EMAIL environment variable is missing."
            elif self._init_error:
                reason = f"Gmail Client Initialization Failed. Error: {self._init_error}"
            else:
                reason = "Unknown service state."

            logging.warning(f"COMM AGENT: Falling back to SIMULATION. Reason: {reason}")
            
            return {
                "message_id": f"simulated-{session_id}",
                "thread_id": f"thread-{session_id}",
                "provider": "simulated",
            }

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._send_via_gmail,
            payload,
        )

    def _send_via_gmail(self, payload: Dict[str, str]) -> Dict[str, str]:
        """Blocking Gmail API call executed in a thread pool."""
        message = MIMEText(payload["body"])
        message["to"] = ", ".join(payload["approvers"])
        message["from"] = self.sender_email
        message["subject"] = payload["subject"]
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        try:
            result = (
                self._gmail_service.users()
                .messages()
                .send(userId="me", body={"raw": raw_message})
                .execute()
            )
            logging.info("COMM AGENT: Email dispatched for session %s", payload["session_id"])
            return {
                "message_id": result.get("id", ""),
                "thread_id": result.get("threadId", ""),
                "provider": "gmail",
            }
        except HttpError as http_error:
            logging.error("COMM AGENT: Gmail send failed: %s", http_error)
            raise
