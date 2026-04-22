---
name: send
argument-hint: '[telegram|discord] [message|image|voice] [content or path]'
description: >
  Send a message, image, or voice note to a user via Lyra bots (Telegram or Discord).
  Trigger phrases: "send on telegram", "send image via lyra", "send voice note",
  "send to discord", "notify user on telegram", "push message to telegram".
allowed-tools: Bash
---

# Lyra Send

Send content proactively to a user via the running Lyra bot — without waiting for them
to speak first.

Supports: **text message**, **image** (file or URL), **voice/audio** (file).

## Entry

```
/send                                       → guided mode (asks everything)
/send telegram message "Deploy done ✅"
/send telegram image /tmp/diagram.png "Here's the diagram"
/send discord message "Reminder: standup in 5 min"
/send telegram voice /tmp/note.ogg
```

## Step 1 — Resolve Arguments

Parse $ARGUMENTS if present:
- Token 1: platform → `telegram` or `discord`
- Token 2: type → `message`, `image`, or `voice`
- Remaining tokens: content (text) or path

Missing pieces → use DP protocol (load `${CLAUDE_PLUGIN_ROOT}/../shared/references/decision-presentation.md`):

| Missing | Pattern | Prompt |
|---------|---------|--------|
| platform | DP(A) | "Which platform?" — **Telegram** · **Discord** |
| type | DP(A) | "What to send?" — **Message** · **Image** · **Voice** |
| content | DP(B) | "What's the content / file path?" (plain input) |

## Step 2 — Get Target User / Channel ID

### Finding the ID

**Telegram — chat_id:**
The chat_id is the numeric ID of the conversation. Find it in Lyra's turn history:

```bash
python3 - <<'EOF'
from pathlib import Path
import sqlite3, json

lyra_dir = Path.home() / '.lyra'
conn = sqlite3.connect(lyra_dir / 'turns.db')
# Show recent unique telegram chat IDs with last message preview
rows = conn.execute("""
    SELECT platform_meta, content, created_at
    FROM turns
    WHERE platform = 'telegram' AND role = 'user'
    ORDER BY created_at DESC
    LIMIT 20
""").fetchall()
seen = set()
for meta_raw, content, ts in rows:
    try:
        meta = json.loads(meta_raw) if meta_raw else {}
        chat_id = meta.get('chat_id')
        if chat_id and chat_id not in seen:
            seen.add(chat_id)
            print(f"chat_id={chat_id}  ({ts[:16]})  \"{content[:60]}\"")
    except Exception:
        pass
EOF
```

**Discord — channel_id or thread_id:**
```bash
python3 - <<'EOF'
from pathlib import Path
import sqlite3, json

lyra_dir = Path.home() / '.lyra'
conn = sqlite3.connect(lyra_dir / 'turns.db')
rows = conn.execute("""
    SELECT platform_meta, content, created_at
    FROM turns
    WHERE platform = 'discord' AND role = 'user'
    ORDER BY created_at DESC
    LIMIT 20
""").fetchall()
seen = set()
for meta_raw, content, ts in rows:
    try:
        meta = json.loads(meta_raw) if meta_raw else {}
        cid = meta.get('thread_id') or meta.get('channel_id')
        if cid and cid not in seen:
            seen.add(cid)
            print(f"channel/thread_id={cid}  ({ts[:16]})  \"{content[:60]}\"")
    except Exception:
        pass
EOF
```

Show the results to the user and ask which ID to use via DP(A).
If only one result → use it directly without asking.

Store as `TARGET_ID`.

## Step 3 — Send

Decrypt the bot token and call the API in a single script. The token is never printed
or stored outside the script's local variable.

### Telegram — Text message

```bash
python3 - <<'EOF'
from pathlib import Path
from cryptography.fernet import Fernet
import sqlite3, requests

lyra_dir = Path.home() / '.lyra'
key = (lyra_dir / 'keyring.key').read_bytes()
f = Fernet(key)
conn = sqlite3.connect(lyra_dir / 'config.db')
row = conn.execute(
    'SELECT token FROM bot_secrets WHERE bot_id=? AND platform=?',
    ('lyra', 'telegram')
).fetchone()
if not row:
    print("ERROR: no token found for lyra/telegram")
    raise SystemExit(1)
token = f.decrypt(row[0].encode()).decode()

r = requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={"chat_id": TARGET_ID, "text": "CONTENT", "parse_mode": "Markdown"}
)
print(r.status_code, r.json().get('ok'), r.json().get('description', ''))
EOF
```

### Telegram — Image (local file)

```bash
python3 - <<'EOF'
from pathlib import Path
from cryptography.fernet import Fernet
import sqlite3, requests

lyra_dir = Path.home() / '.lyra'
key = (lyra_dir / 'keyring.key').read_bytes()
f = Fernet(key)
conn = sqlite3.connect(lyra_dir / 'config.db')
row = conn.execute(
    'SELECT token FROM bot_secrets WHERE bot_id=? AND platform=?',
    ('lyra', 'telegram')
).fetchone()
if not row:
    print("ERROR: no token found for lyra/telegram")
    raise SystemExit(1)
token = f.decrypt(row[0].encode()).decode()

with open("FILE_PATH", "rb") as fh:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data={"chat_id": TARGET_ID, "caption": "CAPTION"},
        files={"photo": fh}
    )
print(r.status_code, r.json().get('ok'), r.json().get('description', ''))
EOF
```

### Telegram — Image (URL)

```bash
python3 - <<'EOF'
from pathlib import Path
from cryptography.fernet import Fernet
import sqlite3, requests

lyra_dir = Path.home() / '.lyra'
key = (lyra_dir / 'keyring.key').read_bytes()
f = Fernet(key)
conn = sqlite3.connect(lyra_dir / 'config.db')
row = conn.execute(
    'SELECT token FROM bot_secrets WHERE bot_id=? AND platform=?',
    ('lyra', 'telegram')
).fetchone()
if not row:
    print("ERROR: no token found for lyra/telegram")
    raise SystemExit(1)
token = f.decrypt(row[0].encode()).decode()

r = requests.post(
    f"https://api.telegram.org/bot{token}/sendPhoto",
    json={"chat_id": TARGET_ID, "photo": "IMAGE_URL", "caption": "CAPTION"}
)
print(r.status_code, r.json().get('ok'), r.json().get('description', ''))
EOF
```

### Telegram — Voice / Audio (local file)

```bash
python3 - <<'EOF'
from pathlib import Path
from cryptography.fernet import Fernet
import sqlite3, requests

lyra_dir = Path.home() / '.lyra'
key = (lyra_dir / 'keyring.key').read_bytes()
f = Fernet(key)
conn = sqlite3.connect(lyra_dir / 'config.db')
row = conn.execute(
    'SELECT token FROM bot_secrets WHERE bot_id=? AND platform=?',
    ('lyra', 'telegram')
).fetchone()
if not row:
    print("ERROR: no token found for lyra/telegram")
    raise SystemExit(1)
token = f.decrypt(row[0].encode()).decode()

with open("FILE_PATH", "rb") as fh:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendVoice",
        data={"chat_id": TARGET_ID},
        files={"voice": fh}
    )
print(r.status_code, r.json().get('ok'), r.json().get('description', ''))
EOF
```

### Discord — Text message

```bash
python3 - <<'EOF'
from pathlib import Path
from cryptography.fernet import Fernet
import sqlite3, requests

lyra_dir = Path.home() / '.lyra'
key = (lyra_dir / 'keyring.key').read_bytes()
f = Fernet(key)
conn = sqlite3.connect(lyra_dir / 'config.db')
row = conn.execute(
    'SELECT token FROM bot_secrets WHERE bot_id=? AND platform=?',
    ('lyra', 'discord')
).fetchone()
if not row:
    print("ERROR: no token found for lyra/discord")
    raise SystemExit(1)
token = f.decrypt(row[0].encode()).decode()

r = requests.post(
    f"https://discord.com/api/v10/channels/TARGET_ID/messages",
    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
    json={"content": "CONTENT"}
)
print(r.status_code, r.json().get('id', r.text[:100]))
EOF
```

### Discord — Image (local file)

```bash
python3 - <<'EOF'
from pathlib import Path
from cryptography.fernet import Fernet
import sqlite3, requests

lyra_dir = Path.home() / '.lyra'
key = (lyra_dir / 'keyring.key').read_bytes()
f = Fernet(key)
conn = sqlite3.connect(lyra_dir / 'config.db')
row = conn.execute(
    'SELECT token FROM bot_secrets WHERE bot_id=? AND platform=?',
    ('lyra', 'discord')
).fetchone()
if not row:
    print("ERROR: no token found for lyra/discord")
    raise SystemExit(1)
token = f.decrypt(row[0].encode()).decode()

with open("FILE_PATH", "rb") as fh:
    r = requests.post(
        f"https://discord.com/api/v10/channels/TARGET_ID/messages",
        headers={"Authorization": f"Bot {token}"},
        data={"content": "CAPTION"},
        files={"file": ("image.png", fh, "image/png")}
    )
print(r.status_code, r.json().get('id', r.text[:100]))
EOF
```

If output starts with `ERROR` → stop and tell the user: "No Lyra bot token found for
`{platform}`. Make sure `lyra agent init` has been run."

## Step 4 — Confirm

If `ok: True` (Telegram) or status 200 (Discord) → "✅ Sent successfully."
Otherwise → show the error message and suggest checking:
- Token validity (`lyra agent init --force`)
- That the bot has previously spoken with this user/channel (Telegram bots can't
  initiate conversations with users who have never messaged them first)
- Discord: that the bot has permission to post in the target channel

$ARGUMENTS
