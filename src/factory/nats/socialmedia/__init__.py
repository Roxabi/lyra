"""Hub-side social media tool client."""

from factory.nats.socialmedia.nats_socialmedia_client import (
    NatsSocialMediaClient,
    SocialMediaUnavailableError,
)

__all__ = ["NatsSocialMediaClient", "SocialMediaUnavailableError"]