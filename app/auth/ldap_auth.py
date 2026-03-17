import logging

logger = logging.getLogger(__name__)


def authenticate_ldap(username, password, app_config):
    """Authenticate user against LDAP server.

    Returns dict with user info on success, None on failure.
    """
    try:
        import ldap
    except ImportError:
        logger.warning('python-ldap not installed, LDAP authentication unavailable')
        return None

    ldap_url = app_config.get('LDAP_URL', '')
    base_dn = app_config.get('LDAP_BASE_DN', '')
    user_dn = app_config.get('LDAP_USER_DN', 'ou=users')

    if not ldap_url or not base_dn:
        logger.warning('LDAP not configured')
        return None

    user_dn_full = f'uid={username},{user_dn},{base_dn}'

    try:
        conn = ldap.initialize(ldap_url)
        conn.protocol_version = ldap.VERSION3
        conn.set_option(ldap.OPT_REFERRALS, 0)
        conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 10)

        conn.simple_bind_s(user_dn_full, password)

        search_filter = f'(uid={ldap.filter.escape_filter_chars(username)})'
        result = conn.search_s(f'{user_dn},{base_dn}', ldap.SCOPE_SUBTREE, search_filter,
                               ['cn', 'mail', 'uid'])

        if result:
            dn, attrs = result[0]
            return {
                'username': username,
                'display_name': attrs.get('cn', [username.encode()])[0].decode('utf-8'),
                'email': attrs.get('mail', [f'{username}@ldap'.encode()])[0].decode('utf-8'),
            }

        conn.unbind_s()
        return {'username': username, 'display_name': username, 'email': f'{username}@ldap'}

    except Exception as e:
        logger.error(f'LDAP authentication failed for {username}: {e}')
        return None
