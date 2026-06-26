# Backing provider mapping (v1: Postiz Public API)

Factory ``factory.tool.socialmedia.*`` ↔ provider HTTP. **Configuration is UI-only**
on the backing service; Factory never creates orgs, groups, or OAuth connections.

## Operator setup (Postiz frontend, one-time)

| Provider concept | UI | Factory contract |
|------------------|-----|------------------|
| Organization | signup / settings | implicit via API key secret |
| Group / Customer | channel groups | `brand_slug` |
| Integration | OAuth connect | resolved via `brand_slug` + `platforms` |

**Slug rule:** display name `Enichu` → `brand_slug=enichu`.

**Telegram:** not connected in Postiz — Factory owns Telegram.

## Auth

| Layer | Credential |
|-------|------------|
| Satellite → Postiz HTTP | `Authorization: <org-api-key>` |
| Agent → satellite | NATS nkey ACL only |

## HTTP base (v1)

`{SOCIALMEDIA_PROVIDER_BASE_URL}/public/v1`  
Example: `http://postiz-app:5000/api/public/v1` (dual-network: `roxabi.network` + `postiz.network`)

## Operation mapping

| NATS subject | Postiz endpoint |
|--------------|-----------------|
| `list_groups` | `GET /groups` |
| `list_integrations` | `GET /integrations?group={id}` |
| `publish` | `POST /posts` (`type: "now"`) |
| `schedule` | `POST /posts` (`type: "schedule"`, `date`) |

Media: adapter `GET` blob from Factory BlobStore → `POST /upload` → `MediaDto` in post body.

## NATS subjects

| Subject | Request | Response |
|---------|---------|----------|
| `factory.tool.socialmedia.list_groups` | `SocialMediaListGroupsRequest` | `SocialMediaListGroupsResponse` |
| `factory.tool.socialmedia.list_integrations` | `SocialMediaListIntegrationsRequest` | `SocialMediaListIntegrationsResponse` |
| `factory.tool.socialmedia.publish` | `SocialMediaPublishRequest` | `SocialMediaPublishResponse` |
| `factory.tool.socialmedia.schedule` | `SocialMediaScheduleRequest` | `SocialMediaPublishResponse` |
| `factory.tool.socialmedia.heartbeat` | `SocialMediaHeartbeat` | — |