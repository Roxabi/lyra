"""GraphQL transport + query templates for corpus sync."""

from __future__ import annotations

import json
import subprocess
from typing import Any


class GraphQLError(RuntimeError):
    pass


ISSUES_QUERY = """
query($owner: String!, $name: String!, $cursor: String, $since: DateTime) {
  repository(owner: $owner, name: $name) {
    issues(
      first: 100
      after: $cursor
      filterBy: { since: $since }
      orderBy: { field: UPDATED_AT, direction: ASC }
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        state
        url
        createdAt
        updatedAt
        closedAt
        milestone { title }
        labels(first: 30) { nodes { name } }
        trackedIssues(first: 50) { nodes { number repository { nameWithOwner } } }
        trackedInIssues(first: 50) { nodes { number repository { nameWithOwner } } }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""


REPOS_QUERY = """
query($org: String!, $cursor: String) {
  organization(login: $org) {
    repositories(
      first: 100
      after: $cursor
      isArchived: false
      orderBy: { field: NAME, direction: ASC }
    ) {
      pageInfo { hasNextPage endCursor }
      nodes { name owner { login } isArchived }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""


STUB_ISSUE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number title state url createdAt updatedAt closedAt
    }
  }
  rateLimit { cost remaining resetAt }
}
"""


def _build_variable_flags(variables: dict[str, Any]) -> list[str]:
    """Build -F / -f flags for `gh api graphql` from a variables dict.

    Uses -F (typed) for int/bool/None values so gh infers the JSON type,
    and -f (raw string) for str values.
    """
    flags: list[str] = []
    for key, value in variables.items():
        if value is None:
            flags += ["-F", f"{key}=null"]
        elif isinstance(value, bool):
            flags += ["-F", f"{key}={str(value).lower()}"]
        elif isinstance(value, int):
            flags += ["-F", f"{key}={value}"]
        else:
            flags += ["-f", f"{key}={value}"]
    return flags


def gh_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Execute a GraphQL query via `gh api graphql` subprocess.

    Raises GraphQLError if the response contains an "errors" key or gh exits non-zero.
    """
    flags = _build_variable_flags(variables)
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}", *flags],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GraphQLError(f"gh exited {result.returncode}: {result.stderr.strip()}")
    try:
        response: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GraphQLError(f"non-JSON response: {result.stdout[:200]}") from exc
    if "errors" in response:
        raise GraphQLError(f"GraphQL errors: {response['errors']}")
    return response
