# Part of Odoo. See LICENSE file for full copyright and licensing details.

import hashlib
import hmac
import json
import logging
import re
import secrets

import requests
from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.http import request

from odoo.addons.ecommerce_shopify import utils_graphql as shopify_utils_graphql

_logger = logging.getLogger(__name__)


class ShopifyController(http.Controller):

    shopify_app_url = '/odoo/ecommerce.account/<int:account_id>/shopify/app'
    shopify_auth_callback = '/odoo/ecommerce.account/<int:account_id>/shopify/oauth/callback'

    @http.route(shopify_app_url, type='http')
    def shopify_app_entry(self, account_id, **data):
        _logger.info("Received request for shopify app installation with account_id: %d", account_id)
        received_hmac = data.get('hmac')
        if not received_hmac:
            _logger.error("HMAC not received in the request.")
            raise Forbidden()

        account = request.env['ecommerce.account'].browse(account_id)
        if not account:
            _logger.error("No account found with ID: %d", account_id)
            raise Forbidden()

        if account.state == 'connected':
            _logger.warning("Account with id %d is already connected.", account_id)
            redirect_url = f'{request.httprequest.url_root}odoo/ecommerce.account/{account_id}'
            html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<script>window.top.location.href = {json.dumps(redirect_url)};</script>
</body>
</html>"""
            return request.make_response(
                html_body,
                headers=[
                    ('Content-Type', 'text/html; charset=utf-8'),
                    ('Content-Security-Policy', "frame-ancestors https://admin.shopify.com https://*.myshopify.com"),
                ],
            )

        data = {k: v for k, v in data.items() if k != 'hmac'}  # remove hmac from data.
        sorted_data = sorted(data.items())  # Sort data alphabetically
        message = '&'.join(f'{k}={v}' for k, v in sorted_data)  # Build message string

        calculated_hmac = hmac.new(
            (account.shopify_client_secret or ' ').encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()  # Generate HMAC-SHA256

        if not hmac.compare_digest(calculated_hmac, received_hmac):
            _logger.error("HMAC mismatch: calculated HMAC does not match received HMAC.")
            raise Forbidden("Signature verification has failed.")

        scopes = [
            'read_locations',
            'read_orders',
            'write_assigned_fulfillment_orders',
            'read_merchant_managed_fulfillment_orders',
            'write_merchant_managed_fulfillment_orders',
            'read_fulfillments',
            'write_fulfillments',
            'read_third_party_fulfillment_orders',
            'write_third_party_fulfillment_orders',
            'read_assigned_fulfillment_orders',
            'write_inventory',
            'read_inventory',
            'read_customers',
            'read_products',
            'write_products',
            'read_publications',
            'write_publications',
        ]
        nonce = secrets.token_urlsafe(32)
        redirect_uri = f'{request.httprequest.url_root}odoo/ecommerce.account/{account_id}/shopify/oauth/callback'
        oauth_url = (
            f"https://{data.get('shop')}/admin/oauth/authorize?"
            f"client_id={account.shopify_client_id}&"
            f"scope={','.join(scopes)}&"
            f"redirect_uri={redirect_uri}&state={nonce}"
        )

        # Shopify embeds the app URL in an iframe inside admin.shopify.com.
        # A plain 302 redirect fails because X-Frame-Options blocks the iframe.
        # Content-Security-Policy frame-ancestors takes precedence over X-Frame-Options,
        # and window.top.location.href escapes the iframe so OAuth runs in the full window.
        html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Connecting to Shopify...</title></head>
<body>
<script>window.top.location.href = {json.dumps(oauth_url)};</script>
</body>
</html>"""
        response = request.make_response(
            html_body,
            headers=[
                ('Content-Type', 'text/html; charset=utf-8'),
                ('Content-Security-Policy', "frame-ancestors https://admin.shopify.com https://*.myshopify.com"),
            ]
        )
        response.status_code = 200
        response.set_cookie(
            'shopify_oauth_state',
            nonce,
            httponly=True,
            secure=True,
            samesite='None',
        )
        _logger.info("Response set with top-frame redirect to OAuth URL.")
        return response

    @http.route(shopify_auth_callback, type='http')
    def handle_shopify_oauth_callback(self, account_id, **data):
        _logger.info("Received OAuth callback from shopify with account_id: %d", account_id)
        account = request.env['ecommerce.account'].browse(account_id)
        if not account:
            _logger.error("No account found with ID: %d", account_id)
            raise Forbidden()

        state_from_query = data.get('state')
        state_from_cookie = request.httprequest.cookies.get('shopify_oauth_state')
        if not state_from_query or not state_from_cookie:
            _logger.error("Missing OAuth state: state_from_query=%s, state_from_cookie=%s", state_from_query, state_from_cookie)
            raise Forbidden("Missing OAuth state")
        if not hmac.compare_digest(state_from_query, state_from_cookie):
            _logger.error("Invalid OAuth state: state_from_query=%s, state_from_cookie=%s", state_from_query, state_from_cookie)
            raise Forbidden("Invalid OAuth state")

        pattern = r'^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com'
        shop = data.get('shop')
        if not re.fullmatch(pattern, shop):
            _logger.error("Invalid Shopify domain: %s", shop)
            raise Forbidden()

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
        }
        params = {
            'client_id': account.shopify_client_id,
            'client_secret': account.shopify_client_secret,
            'code': data.get('code'),
        }
        try:
            response = requests.post(
                url=f'https://{shop}/admin/oauth/access_token',
                headers=headers,
                params=params,
            )
            response.raise_for_status()
        except Exception as e:
            _logger.error('Exception occurred during generate access token from oauth code: %s in shopify, error_description: %s', data.get('code'), e)
            raise Forbidden("Something went wrong during generate access token from oauth code: %s in shopify, error_description: %s", data.get('code'), str(e))
        account.write({
            'shopify_store': data.get('shop').removesuffix('.myshopify.com'),
            'shopify_access_token': response.json().get('access_token'),
        })
        _logger.info("Received access token for shop: %s", shop)
        account.action_connect()

        response = shopify_utils_graphql.call_shopify_graphql_query(
            account=account,
            endpoint='shop',
        )
        account.shopify_store_name = (response.get('shop') or {}).get('name')
        return request.redirect(f'{request.httprequest.url_root}odoo/ecommerce.account/{account_id}')
