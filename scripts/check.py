import argparse
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def run(arguments, project, environment):
    print(f"[{project}] {' '.join(arguments)}", flush=True)
    subprocess.run(arguments, cwd=ROOT / project, env=environment, check=True)


def check_backend(environment):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    source = make_url(os.getenv('TEST_DATABASE_URL', 'postgresql+psycopg:///postgres'))
    if source.get_backend_name() != 'postgresql':
        raise ValueError('TEST_DATABASE_URL must use PostgreSQL')
    if source.database != 'postgres' and not (source.database or '').endswith('_test'):
        raise ValueError('TEST_DATABASE_URL must name postgres or an isolated *_test database')
    database = 'jingpo_checks_' + uuid4().hex[:16] + '_test'
    control = create_engine(source.set(database='postgres'), isolation_level='AUTOCOMMIT')
    created = False
    try:
        with control.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
        print(f'[be] Temporary database: {database}', flush=True)
        environment['DATABASE_URL'] = source.set(database=database).render_as_string(hide_password=False)
        for arguments in [
            [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
            [sys.executable, '-m', 'alembic', 'check'],
            [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
        ]:
            run(arguments, 'be', environment)
    finally:
        try:
            if created:
                with control.connect() as connection:
                    connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
                print(f'[be] Removed temporary database: {database}', flush=True)
        finally:
            control.dispose()


def main():
    parser = argparse.ArgumentParser(description='Run isolated project checks')
    parser.add_argument('project', choices=['be', 'agent'])
    project = parser.parse_args().project
    environment = dict(os.environ)
    environment.update({
        'PYTHON_DOTENV_DISABLED': '1',
        'LLM_PROVIDER': 'deepseek',
        'LLM_MODEL': 'controlled-test-model',
        'LLM_API_KEY': 'controlled-test-key',
        'DEEPSEEK_API_KEY': 'controlled-test-key',
        'DASHSCOPE_API_KEY': 'controlled-test-key',
        'LLM_BASE_URL': 'http://127.0.0.1:9',
        'BE_BASE_URL': 'http://127.0.0.1:9',
        'AGENT_BASE_URL': 'http://127.0.0.1:9',
        'AGENT_DEBUG': 'false',
        'EMAIL_PROVIDER': 'smtp',
        'SMTP_HOST': '127.0.0.1',
        'SMTP_PORT': '9',
        'SMTP_STARTTLS': 'false',
        'SMTP_FROM': 'checks@example.com',
        'AUTH_COOKIE_SECURE': 'false',
        'AUTH_FRONTEND_URL': 'http://localhost:5173',
        'GOOGLE_CLIENT_ID': '',
        'GOOGLE_CLIENT_SECRET': '',
        'GOOGLE_REDIRECT_URI': '',
        'SCHEDULE_SIGNING_KEY': 'isolated-check-signing-key',
        'RUN_V2_MIGRATION_TESTS': '0',
        'RUN_V8_MIGRATION_TESTS': '0',
        'RUN_V8_SMTP_TESTS': '0',
    })
    run([sys.executable, '-m', 'pip', 'check'], project, environment)
    if project == 'be':
        check_backend(environment)
    else:
        run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], project, environment)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
