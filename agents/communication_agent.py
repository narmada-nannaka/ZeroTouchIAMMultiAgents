"""Communication agent responsible for sending approval emails."""

import asyncio
import base64
import logging
import os
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional

# Imports for Enterprise Auth
from google.oauth2 import service_account
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
            # --- AUTHENTICATION STRATEGY (ENTERPRISE) ---
            
            # 1. Look for Service Account Key (The "Master Key")
            sa_key_path = os.environ.get("SA_KEY_PATH", "orchestrator_key.json")
            
            if os.path.exists(sa_key_path):
                logging.info(f"COMM AGENT: Found Service Account Key at {sa_key_path}.")
                logging.info(f"COMM AGENT: Attempting to impersonate user: {sender_email}")
                
                # Load credentials from the JSON key
                creds = service_account.Credentials.from_service_account_file(
                    sa_key_path, 
                    scopes=[self.GMAIL_SCOPE]
                )

                # CRITICAL: This is the Domain-Wide Delegation magic.
                # The Service Account "becomes" the user specified in sender_email.
                delegated_creds = creds.with_subject(sender_email)
                
                self._gmail_service = build("gmail", "v1", credentials=delegated_creds, cache_discovery=False)
                logging.info(f"COMM AGENT: Successfully authorized as {sender_email} via Domain-Wide Delegation.")

            else:
                raise FileNotFoundError(f"No credential file found. Checked '{sa_key_path}' and 'token.json'.")

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
        # ...
        # Construct the Links
        base_url = self.approval_callback_url.rstrip('/') 
        # We assume callback_url is like "https://service-url.run.app"
        # The route we added in app.py is "/respond"
        
        results = []
        
        # --- LOOP CHANGE: Send individual email to each approver ---
        for approver in approvers:
            
            # 1. Generate UNIQUE Links for this specific approver
            # This fixes the parameter passing issue
            approve_link = f"{base_url}/respond?session_id={session_id}&action=APPROVED&approver_email={approver}"
            deny_link = f"{base_url}/respond?session_id={session_id}&action=DENIED&approver_email={approver}"

            subject = f"[ACTION REQUIRED] Access request for {requester_email}"
            # Use HTML Body for buttons
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif;">
                    <h2 style="color: #1a73e8;">IAM Access Request</h2>
                    <p><strong>User:</strong> {requester_email}</p>
                    <p><strong>Role:</strong> {requested_role}</p>
                    <p><strong>Scope:</strong> {project_scope}</p>
                    
                    <div style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #1a73e8; margin: 20px 0;">
                        <strong>Policy Justification (AI Generated):</strong><br/>
                        {justification_text}
                    </div>
                    
                    <p>As the designated approver, {approver}, please verify this request:</p>
                    
                    <div style="margin-top: 25px;">
                        <a href="{approve_link}" style="background-color: #1e8e3e; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; margin-right: 15px;">APPROVE</a>
                        <a href="{deny_link}" style="background-color: #d93025; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold;">DENY</a>
                    </div>
                    
                    <p style="font-size: 12px; color: #5f6368; margin-top: 40px;">
                        Session ID: {session_id}<br>
                        Zero-Touch IAM Orchestrator
                    </p>
                </body>
            </html>
            """

            payload = {
                "subject": subject,
                "body": html_body, # Pass HTML content
                "recipient": approver,
                "session_id": session_id,
            }

            # --- 2. SIMULATION MODE CHECK (Your requested feature) ---
            if not self.sender_email or not self._gmail_service:
                logging.warning(f"⚠️ [SIMULATION] Sending to {approver}")
                logging.warning(f"🔗 CLICK TO APPROVE: {approve_link}")
                
                results.append({
                    "approver": approver, 
                    "status": "simulated", 
                    "link": approve_link # Useful for debugging
                })
                continue # Skip actual sending

            # Send Real Email
            loop = asyncio.get_running_loop()
            try:
                res = await loop.run_in_executor(None, self._send_via_gmail, payload)
                results.append(res)
            except Exception as e:
                logging.error(f"Failed to send to {approver}: {e}")
                results.append({"approver": approver, "status": "failed", "error": str(e)})

        # Return summary
        return {
            "status": "partial_success" if any(r.get("status") == "failed" for r in results) else "sent",
            "details": results,
            "count": len(results)
        }

    def _send_via_gmail(self, payload: Dict[str, str]) -> Dict[str, str]:
        """Blocking Gmail API call executed in a thread pool."""
        message = MIMEMultipart("alternative")
        message["to"] = (payload["recipient"])
        message["from"] = self.sender_email
        message["subject"] = payload["subject"]
        
        # Attach HTML part
        part = MIMEText(payload["body"], "html")
        message.attach(part)
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
                "approver": payload["recipient"],
                "status": "sent"
            }
        except HttpError as http_error:
            logging.error("COMM AGENT: Gmail send failed: %s", http_error)
            raise
