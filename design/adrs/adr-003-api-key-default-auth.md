# ADR-003: API Key as Default Auth with OAuth2 M2M as Alternative

**Status:** Accepted
**Date:** 2026-02-21

## Context

Qlik Cloud supports multiple authentication methods for API access:

1. **API Keys**: Generated in Qlik Cloud hub (My Qlik > API Keys). Simple bearer token.
2. **OAuth2 M2M (Client Credentials)**: Machine-to-machine flow. More complex but supports token rotation and scoped permissions.
3. **Interactive OAuth2**: Requires user login. Not suitable for server-to-server.

## Decision

**Chosen: API Key as the default, with OAuth2 M2M as an opt-in alternative.**

## Rationale

1. **Simplicity of onboarding**: API keys require one step — generate in Qlik Cloud hub, paste into config. OAuth2 M2M requires creating an OAuth client, configuring scopes, and managing client ID + secret pairs.

2. **Sufficient for most deployments**: API keys provide full access scoped to the user who generated them. For MCP servers running as a service account, this is adequate.

3. **OAuth2 for production**: Enterprises with strict credential rotation policies will prefer OAuth2 M2M. Supporting it as an alternative covers this use case without imposing complexity on all users.

### Trade-offs accepted:

- **API key expiration**: Qlik Cloud API keys can expire. Users must monitor and rotate them manually. OAuth2 tokens auto-refresh.
- **Scope limitations**: API keys inherit the generating user's full permissions. OAuth2 M2M allows granular scope definition. The API key approach is "all or nothing."

## Consequences

- Config supports both `api_key` and `oauth` blocks (mutually exclusive)
- `AuthManager` auto-detects which mode to use based on config
- OAuth2 token caching and refresh is handled transparently
- Documentation includes setup instructions for both methods
