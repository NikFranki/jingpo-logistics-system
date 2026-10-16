import argparse
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from auth.models import User
from auth.security import normalize_email, normalize_username, password_hash, validate_password
from auth.service import audit, revoke_all
from business_time import begin_business_write
from db import SessionLocal


def main():
    parser = argparse.ArgumentParser(description='在服务器初始化或恢复唯一管理员')
    parser.add_argument('action', choices=['init', 'restore'])
    parser.add_argument('--username', required=True)
    parser.add_argument('--email', required=True)
    args = parser.parse_args()
    username, email = normalize_username(args.username), normalize_email(args.email)
    password = validate_password(getpass('新管理员密码：'))
    if password != getpass('再次输入密码：'):
        raise SystemExit('两次密码不一致')
    encoded = password_hash(password)
    with SessionLocal.begin() as session:
        begin_business_write(session)
        admin = session.scalar(select(User).where(User.role == 'admin').with_for_update())
        if args.action == 'init':
            if admin:
                raise SystemExit('管理员已存在，请使用 restore')
            admin = User(username=username, email=email, password_hash=encoded, role='admin', email_verified=True)
            session.add(admin)
            session.flush()
        else:
            if not admin:
                raise SystemExit('尚无管理员，请先执行 init')
            admin.username, admin.email, admin.password_hash = username, email, encoded
            admin.enabled, admin.email_verified, admin.needs_username = True, True, False
            revoke_all(session, admin)
        audit(session, admin, 'ADMIN_' + args.action.upper(), {'username': username}, admin.id)
    print('管理员已' + ('初始化' if args.action == 'init' else '恢复') + '，请使用新密码登录。')


if __name__ == '__main__':
    try:
        main()
    except IntegrityError:
        raise SystemExit('用户名或邮箱已被其他账号使用') from None
