"""SMTP email backend class."""
import smtplib
import ssl
import threading
import warnings

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.utils import DNS_NAME
from django.core.mail.message import sanitize_address
from django.utils.deprecation import RemovedInDjango20Warning


class EmailBackend(BaseEmailBackend):
    """
    A wrapper that manages the SMTP network connection.
    """
    def __init__(self, host=None, port=None, username=None, password=None,
                 use_tls=None, fail_silently=False, use_ssl=None, timeout=None,
                 **kwargs):
        super(EmailBackend, self).__init__(fail_silently=fail_silently)
        EMAIL_CONFIG = self._get_email_config()
        self.host = host or EMAIL_CONFIG['HOST']
        self.port = port or EMAIL_CONFIG['PORT']
        self.username = EMAIL_CONFIG['USER'] if username is None else username
        self.password = EMAIL_CONFIG['PASSWORD'] if password is None else password
        self.use_tls = EMAIL_CONFIG['USE_TLS'] if use_tls is None else use_tls
        self.use_ssl = EMAIL_CONFIG['USE_SSL'] if use_ssl is None else use_ssl
        self.timeout = timeout
        if self.use_ssl and self.use_tls:
            raise ValueError(
                "EMAIL_CONFIG['USE_TLS']/EMAIL_CONFIG['USE_SSL'] are mutually exclusive, "
                "so only set one of those settings to True.")
        self.connection = None
        self._lock = threading.RLock()

    def _get_email_config(self):
        if settings.is_overridden('EMAIL_CONFIG'):
            config = settings.DEFAULT_EMAIL_CONFIG
            config.update(settings.EMAIL_CONFIG)
            return config

        warnings.warn(
            "Using the old EMAIL_* settings is deprecated and will be "
            "removed in Django 2.0. Use the EMAIL_CONFIG dict instead.",
            RemovedInDjango20Warning)
        return {
            'HOST': settings.EMAIL_HOST,
            'PORT': settings.EMAIL_PORT,
            'USER': settings.EMAIL_HOST_USER,
            'PASSWORD': settings.EMAIL_HOST_PASSWORD,
            'USE_TLS': settings.EMAIL_USE_TLS,
            'USE_SSL': settings.EMAIL_USE_SSL,
        }

    def open(self):
        """
        Ensures we have a connection to the email server. Returns whether or
        not a new connection was required (True or False).
        """
        if self.connection:
            # Nothing to do if the connection is already open.
            return False

        connection_class = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        # If local_hostname is not specified, socket.getfqdn() gets used.
        # For performance, we use the cached FQDN for local_hostname.
        connection_params = {'local_hostname': DNS_NAME.get_fqdn()}
        if self.timeout is not None:
            connection_params['timeout'] = self.timeout
        try:
            self.connection = connection_class(self.host, self.port, **connection_params)

            # TLS/SSL are mutually exclusive, so only attempt TLS over
            # non-secure connections.
            if not self.use_ssl and self.use_tls:
                self.connection.ehlo()
                self.connection.starttls()
                self.connection.ehlo()
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            return True
        except smtplib.SMTPException:
            if not self.fail_silently:
                raise

    def close(self):
        """Closes the connection to the email server."""
        if self.connection is None:
            return
        try:
            try:
                self.connection.quit()
            except (ssl.SSLError, smtplib.SMTPServerDisconnected):
                # This happens when calling quit() on a TLS connection
                # sometimes, or when the connection was already disconnected
                # by the server.
                self.connection.close()
            except smtplib.SMTPException:
                if self.fail_silently:
                    return
                raise
        finally:
            self.connection = None

    def send_messages(self, email_messages):
        """
        Sends one or more EmailMessage objects and returns the number of email
        messages sent.
        """
        if not email_messages:
            return
        with self._lock:
            new_conn_created = self.open()
            if not self.connection:
                # We failed silently on open().
                # Trying to send would be pointless.
                return
            num_sent = 0
            for message in email_messages:
                sent = self._send(message)
                if sent:
                    num_sent += 1
            if new_conn_created:
                self.close()
        return num_sent

    def _send(self, email_message):
        """A helper method that does the actual sending."""
        if not email_message.recipients():
            return False
        from_email = sanitize_address(email_message.from_email, email_message.encoding)
        recipients = [sanitize_address(addr, email_message.encoding)
                      for addr in email_message.recipients()]
        message = email_message.message()
        try:
            self.connection.sendmail(from_email, recipients, message.as_bytes())
        except smtplib.SMTPException:
            if not self.fail_silently:
                raise
            return False
        return True
