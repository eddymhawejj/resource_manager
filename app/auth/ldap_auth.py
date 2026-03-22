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
                               ['cn', 'mail', 'uid', 'memberOf'])

        if result:
            dn, attrs = result[0]
            groups = [g.decode('utf-8') for g in attrs.get('memberOf', [])]
            return {
                'username': username,
                'display_name': attrs.get('cn', [username.encode()])[0].decode('utf-8'),
                'email': attrs.get('mail', [f'{username}@ldap'.encode()])[0].decode('utf-8'),
                'groups': groups,
            }

        conn.unbind_s()
        return {'username': username, 'display_name': username, 'email': f'{username}@ldap', 'groups': []}

    except Exception as e:
        logger.error(f'LDAP authentication failed for {username}: {e}')
        return None


def sync_user_groups(user, ldap_group_dns):
    """Sync user's local group memberships based on LDAP group DNs.

    For each ResourceGroup that has an ldap_dn set:
    - If the user is in that LDAP group, add them as a member
    - If the user is NOT in that LDAP group, remove them
    Groups without an ldap_dn are managed manually and left untouched.
    """
    from app.models import ResourceGroup

    mapped_groups = ResourceGroup.query.filter(ResourceGroup.ldap_dn.isnot(None)).all()
    if not mapped_groups:
        return

    ldap_dns_lower = {dn.lower() for dn in ldap_group_dns}

    for group in mapped_groups:
        in_ldap_group = group.ldap_dn.lower() in ldap_dns_lower
        is_member = user in group.members

        if in_ldap_group and not is_member:
            group.members.append(user)
            logger.info(f'Added user {user.username} to group {group.name} (LDAP sync)')
        elif not in_ldap_group and is_member:
            group.members.remove(user)
            logger.info(f'Removed user {user.username} from group {group.name} (LDAP sync)')
