"""
Misskey to Mastodon Crossposter

This script fetches a user's posts from Misskey and crossposts them to Mastodon.
Posts that are replies, quotes, or contain mentions are excluded from crossposting.
Attachments (images, videos, etc.) are included in the crossposted content.
"""

import os
import json
import time
import mimetypes
import signal
from typing import List, Dict, Any, Optional, Generator
from dataclasses import dataclass
from datetime import datetime
import requests
from requests.exceptions import RequestException


@dataclass
class Config:
    """Configuration for the Misskey to Mastodon crossposter."""

    # Misskey configuration
    misskey_instance: str
    misskey_token: str
    misskey_user_id: str

    # Mastodon configuration
    mastodon_instance: str
    mastodon_token: str

    # General configuration
    fetch_limit: int = 20
    since_id: Optional[str] = None
    crosspost_delay: int = 2  # Seconds between crossposts to avoid rate limits


class MisskeyClient:
    """Client for interacting with the Misskey API."""

    def __init__(self, instance: str, token: str):
        self.instance = instance.rstrip("/")
        self.token = token
        self.base_url = f"{self.instance}/api"
        self._running = True

    def _make_request(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Make a request to the Misskey API."""
        url = f"{self.base_url}/{endpoint}"
        headers = {"Content-Type": "application/json"}
        data["i"] = self.token

        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_user_notes(
        self, user_id: str, limit: int = 20, since_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get notes (posts) from a specific user."""
        data = {
            "userId": user_id,
            "limit": limit,
            "includeReplies": False,  # We don't want replies
            "includeMyRenotes": False,  # We don't want renotes (quotes)
        }

        if since_id:
            data["sinceId"] = since_id

        return self._make_request("users/notes", data)

    def download_attachment(self, file_url: str) -> tuple[bytes, str, str]:
        """Download a file attachment from Misskey."""
        if not file_url.startswith("http"):
            file_url = f"{self.instance}/{file_url.lstrip('/')}"

        response = requests.get(file_url, timeout=10)
        response.raise_for_status()

        # Get the content type and generate a filename
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        extension = mimetypes.guess_extension(content_type) or ""
        filename = f"attachment_{int(time.time())}_{hash(file_url) % 10000}{extension}"

        return response.content, filename, content_type

    def stream_notes(
        self, user_id: str, since_id: Optional[str] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Stream notes from a specific user in real-time."""
        while self._running:
            try:
                notes = self.get_user_notes(user_id, limit=10, since_id=since_id)
                if notes:
                    for note in notes:
                        if since_id is None or note["id"] != since_id:
                            yield note
                            since_id = note["id"]
                time.sleep(5)  # Wait 5 seconds before checking again
            except RequestException as e:
                print(f"Error streaming notes: {str(e)}")
                time.sleep(30)  # Wait longer on error before retrying
                continue

    def stop_streaming(self):
        """Stop the note streaming."""
        self._running = False


class MastodonClient:
    """Client for interacting with the Mastodon API."""

    def __init__(self, instance: str, token: str):
        self.instance = instance.rstrip("/")
        self.token = token
        self.base_url = f"{self.instance}/api/v1"
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make a request to the Mastodon API."""
        url = f"{self.base_url}/{endpoint}"

        if "headers" not in kwargs:
            kwargs["headers"] = self.headers
        else:
            kwargs["headers"].update(self.headers)

        response = requests.request(method, url, **kwargs, timeout=10)
        response.raise_for_status()
        return response.json() if response.content else {}

    def upload_media(
        self,
        file_content: bytes,
        filename: str,
        mime_type: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload media to Mastodon."""
        endpoint = "media"

        files = {"file": (filename, file_content, mime_type)}

        data = {}
        if description:
            data["description"] = description

        return self._make_request("POST", endpoint, files=files, data=data)

    def create_status(
        self, text: str, media_ids: List[str] = None, visibility: str = "public", spoiler_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new status (post) on Mastodon."""
        endpoint = "statuses"

        data = {
            "status": text,
            "visibility": visibility,
            "content_type": "text/markdown",
        }

        if media_ids:
            data["media_ids"] = media_ids

        if spoiler_text:
            data["spoiler_text"] = spoiler_text

        return self._make_request("POST", endpoint, json=data)


def should_crosspost(note: Dict[str, Any]) -> bool:
    """Determine if a note should be crossposted based on our criteria."""
    # Don't crosspost if it's a reply
    if note.get("replyId") is not None:
        return False

    # Don't crosspost if it's a quote (renote with text) or a renote
    if note.get("renoteId") is not None:
        return False

    # Don't crosspost if it contains mentions
    if note.get("mentions") and len(note.get("mentions")) > 0:
        return False

    # Check if text contains @ symbol (might be a mention)
    if note.get("text") and "@" in note.get("text", ""):
        return False

    return True

def misskey_to_mastodon_visibility(misskey_visibility: str) -> str:
    """Convert Misskey visibility to Mastodon visibility."""
    visibility_map = {
        "public": "public",
        "followers": "private",
        "specified": "direct",
        "home": "unlisted",
    }
    return visibility_map.get(misskey_visibility, "public")

def process_misskey_files(
    misskey_client: MisskeyClient,
    mastodon_client: MastodonClient,
    files: List[Dict[str, Any]],
) -> List[str]:
    """Process Misskey files and upload them to Mastodon."""
    media_ids = []

    for file in files:
        try:
            # Download the file from Misskey
            file_content, filename, mime_type = misskey_client.download_attachment(
                file["url"]
            )

            # Upload to Mastodon
            description = file.get("comment")
            media = mastodon_client.upload_media(
                file_content, filename, mime_type, description
            )
            media_ids.append(media["id"])

            # Wait a bit to avoid rate limits
            time.sleep(1)

        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"Error processing file {file.get('name', 'unknown')}: {str(e)}")

    return media_ids


def crosspost(config: Config):
    """Main function to handle crossposting from Misskey to Mastodon."""
    misskey_client = MisskeyClient(config.misskey_instance, config.misskey_token)
    mastodon_client = MastodonClient(config.mastodon_instance, config.mastodon_token)

    # Get recent notes from the user
    notes = misskey_client.get_user_notes(
        config.misskey_user_id, config.fetch_limit, config.since_id
    )

    # Process newer posts first (notes are typically returned in reverse chronological order)
    newest_id = None
    for note in notes:
        if newest_id is None:
            newest_id = note["id"]

        if should_crosspost(note):
            try:
                # Process attachments if any
                media_ids = []
                if "files" in note and note["files"]:
                    media_ids = process_misskey_files(
                        misskey_client, mastodon_client, note["files"]
                    )

                # Create the post on Mastodon
                text = note.get("text", "")

                visibility = misskey_to_mastodon_visibility(
                    note.get("visibility", "public")
                )

                spoiler_text = note.get("cw")

                mastodon_client.create_status(text, media_ids, visibility, spoiler_text)
                print(f"Successfully crossposted note {note['id']}")

                # Wait between posts to avoid rate limits
                time.sleep(config.crosspost_delay)

            except Exception as e:  # pylint: disable=broad-exception-caught
                print(f"Error crossposting note {note['id']}: {str(e)}")

    # Return the newest ID for the next run
    return newest_id


def load_config() -> Config:
    """Load configuration from environment variables or a config file."""
    # Priority: 1. Environment variables, 2. Config file

    # Try to load from environment variables first
    config_dict = {
        "misskey_instance": os.environ.get("MISSKEY_INSTANCE"),
        "misskey_token": os.environ.get("MISSKEY_TOKEN"),
        "misskey_user_id": os.environ.get("MISSKEY_USER_ID"),
        "mastodon_instance": os.environ.get("MASTODON_INSTANCE"),
        "mastodon_token": os.environ.get("MASTODON_TOKEN"),
        "fetch_limit": int(os.environ.get("FETCH_LIMIT", "20")),
        "crosspost_delay": int(os.environ.get("CROSSPOST_DELAY", "2")),
    }

    # If any required config is missing, try to load from config file
    required_fields = [
        "misskey_instance",
        "misskey_token",
        "misskey_user_id",
        "mastodon_instance",
        "mastodon_token",
    ]

    if any(config_dict[field] is None for field in required_fields):
        config_file = os.environ.get("CONFIG_FILE", "config.json")

        try:
            with open(config_file, "r", encoding="utf-8") as f:
                file_config = json.load(f)
                # Update missing values from file
                for key, value in file_config.items():
                    if config_dict.get(key) is None:
                        config_dict[key] = value
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading config file: {str(e)}")

    # Validate that all required fields are present
    missing_fields = [
        field for field in required_fields if config_dict.get(field) is None
    ]
    if missing_fields:
        raise ValueError(f"Missing required configuration: {', '.join(missing_fields)}")

    # Load the since_id from a state file if it exists
    state_file = "crosspost_state.json"
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
            config_dict["since_id"] = state.get("since_id")
    except (FileNotFoundError, json.JSONDecodeError):
        config_dict["since_id"] = None

    return Config(**config_dict)


def save_state(since_id: str):
    """Save the latest processed note ID to a state file."""
    state_file = "crosspost_state.json"
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump({"since_id": since_id, "last_run": datetime.now().isoformat()}, f)


def main():
    """Main entry point for the script."""
    try:
        config = load_config()
        misskey_client = MisskeyClient(config.misskey_instance, config.misskey_token)
        mastodon_client = MastodonClient(
            config.mastodon_instance, config.mastodon_token
        )

        def signal_handler(_signum, _frame):
            """Handle shutdown signals gracefully."""
            print("\nShutting down gracefully...")
            misskey_client.stop_streaming()

        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        print("Starting to listen for new notes...")
        for note in misskey_client.stream_notes(
            config.misskey_user_id, config.since_id
        ):
            if should_crosspost(note):
                try:
                    # Process attachments if any
                    media_ids = []
                    if "files" in note and note["files"]:
                        media_ids = process_misskey_files(
                            misskey_client, mastodon_client, note["files"]
                        )

                    # Create the post on Mastodon
                    text = note.get("text", "")

                    visibility = misskey_to_mastodon_visibility(
                        note.get("visibility", "public")
                    )

                    spoiler_text = note.get("cw")

                    mastodon_client.create_status(text, media_ids, visibility, spoiler_text)
                    print(f"Successfully crossposted note {note['id']}")

                    # Wait between posts to avoid rate limits
                    time.sleep(config.crosspost_delay)

                except Exception as e:  # pylint: disable=broad-exception-caught
                    print(f"Error crossposting note {note['id']}: {str(e)}")

            # Update the since_id after processing each note
            config.since_id = note["id"]
            save_state(note["id"])

    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"Error in main function: {str(e)}")
    finally:
        misskey_client.stop_streaming()


if __name__ == "__main__":
    main()
