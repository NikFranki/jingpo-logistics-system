import base64
import json
import os
import unittest
from email import message_from_bytes
from unittest.mock import patch

from auth.service import email_is_configured, send_email
from errors import NetworkError


class EmailDeliveryTests(unittest.TestCase):
    def test_provider_configuration_is_checked(self):
        with patch.dict(os.environ, {'EMAIL_PROVIDER': 'gmail_api'}, clear=True):
            self.assertFalse(email_is_configured())
        with patch.dict(os.environ, {'EMAIL_PROVIDER': 'smtp', 'SMTP_HOST': 'mailpit'}, clear=True):
            self.assertTrue(email_is_configured())
        with patch.dict(os.environ, {
            'EMAIL_PROVIDER': 'gmail_api',
            'GMAIL_API_CLIENT_ID': 'client',
            'GMAIL_API_CLIENT_SECRET': 'secret',
            'GMAIL_API_REFRESH_TOKEN': 'refresh',
            'GMAIL_API_SENDER': 'sender@gmail.com',
        }, clear=True):
            self.assertTrue(email_is_configured())

    @patch('auth.service.urlopen')
    def test_gmail_api_refreshes_access_token_and_sends_message(self, mocked_urlopen):
        token_response = unittest.mock.MagicMock()
        token_response.__enter__.return_value.read.return_value = b'{"access_token":"access"}'
        mocked_urlopen.return_value = token_response
        environment = {
            'EMAIL_PROVIDER': 'gmail_api',
            'GMAIL_API_CLIENT_ID': 'client',
            'GMAIL_API_CLIENT_SECRET': 'secret',
            'GMAIL_API_REFRESH_TOKEN': 'refresh',
            'GMAIL_API_SENDER': 'sender@gmail.com',
            'AUTH_FRONTEND_URL': 'https://jingpo.example',
        }
        with patch.dict(os.environ, environment, clear=True):
            send_email('recipient@example.com', 'verify', 'opaque-token')

        token_request = mocked_urlopen.call_args_list[0].args[0]
        self.assertEqual(token_request.full_url, 'https://oauth2.googleapis.com/token')
        self.assertIn(b'refresh_token=refresh', token_request.data)
        send_request = mocked_urlopen.call_args_list[1].args[0]
        self.assertEqual(send_request.full_url,
                         'https://gmail.googleapis.com/gmail/v1/users/me/messages/send')
        self.assertEqual(send_request.get_header('Authorization'), 'Bearer access')
        payload = json.loads(send_request.data)
        encoded = payload['raw']
        decoded = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        message = message_from_bytes(decoded)
        self.assertEqual(message['From'], 'sender@gmail.com')
        self.assertEqual(message['To'], 'recipient@example.com')
        self.assertIn('opaque-token', message.get_payload(decode=True).decode())

    @patch('auth.service.smtplib.SMTP')
    def test_smtp_remains_available(self, mocked_smtp):
        with patch.dict(os.environ, {
            'EMAIL_PROVIDER': 'smtp', 'SMTP_HOST': 'mailpit',
            'SMTP_PORT': '1025', 'SMTP_STARTTLS': 'false',
        }, clear=True):
            send_email('recipient@example.com', 'verify', 'opaque-token')
        mocked_smtp.assert_called_once_with('mailpit', 1025, timeout=10)
        mocked_smtp.return_value.__enter__.return_value.send_message.assert_called_once()

    @patch('auth.service.urlopen', side_effect=OSError('network unavailable'))
    def test_gmail_api_failure_returns_delivery_error(self, _):
        environment = {
            'EMAIL_PROVIDER': 'gmail_api',
            'GMAIL_API_CLIENT_ID': 'client',
            'GMAIL_API_CLIENT_SECRET': 'secret',
            'GMAIL_API_REFRESH_TOKEN': 'refresh',
            'GMAIL_API_SENDER': 'sender@gmail.com',
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(NetworkError) as raised:
                send_email('recipient@example.com', 'reset', 'opaque-token')
        self.assertEqual(raised.exception.code, 'EMAIL_DELIVERY_FAILED')


if __name__ == '__main__':
    unittest.main()
