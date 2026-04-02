/// Simplified JWT/token validation for PoC.
///
/// For the PoC, we just check that a Bearer token exists in the Authorization header.
/// In production, this would validate a real JWT with the shared JWT_SECRET.

#[derive(Debug, Clone)]
pub struct AuthInfo {
    pub token: String,
}

/// Validate the authorization header.
/// Returns Ok(AuthInfo) if the token is present, Err with a message otherwise.
pub fn validate_auth(auth_header: Option<&str>) -> Result<AuthInfo, &'static str> {
    let header_value = auth_header.ok_or("missing Authorization header")?;

    let token = header_value
        .strip_prefix("Bearer ")
        .ok_or("Authorization header must start with 'Bearer '")?;

    if token.is_empty() {
        return Err("empty bearer token");
    }

    Ok(AuthInfo {
        token: token.to_string(),
    })
}
