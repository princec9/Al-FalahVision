# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64

import requests
from requests.exceptions import RequestException

from odoo.exceptions import UserError


def ecommerce_checks_and_cleanup(env, channel_code):
    """checks and cleanup data related to the given ecommerce channel during module uninstallation."""
    account = env['ecommerce.account'].search([('channel_code', '=', channel_code)])
    if account:
        raise UserError(env._("Please uninstall the ecommerce account(s) related to %s module before uninstalling it.", channel_code))
    env['ecommerce.channel'].search([('code', '=', channel_code)]).unlink()


def ensure_account_is_authenticated(account):
    """Ensure that the ecommerce account is authenticated and ready to use."""
    account.ensure_one()
    if account.state == 'disconnected':
        raise UserError(account.env._("The %s account is disconnected. Please connect it first.", account.ecommerce_channel_id.name))
    return True


def fetch_image_base64_from_url(image_url):
    """
    Fetch image from public URL and return base64 encoded bytes
    """
    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        return base64.b64encode(response.content)
    except RequestException as error:
        raise ECommerceApiError(str(error)) from error


class ECommerceApiError(Exception):
    """Custom exception for ECommerce API request errors."""
    pass


class ECommerceAccountWideError(ECommerceApiError):
    """Custom exception for account-wide ECommerce errors like Rate Limits, Authentication issues, API downtime, etc."""
    pass
