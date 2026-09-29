# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import hashlib
import hmac
import json
import logging
from functools import wraps

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


def verify_shopify_webhook_hmac(handler):
    """Reusable Shopify webhook HMAC verifier for controller routes."""
    @wraps(handler)
    def wrapper(self, account_id, *args, **kwargs):
        account = request.env['ecommerce.account'].sudo().browse(account_id).exists()
        if not account:
            return self._json_response(
                {'success': False, 'message': f'E-commerce account {account_id} not found'},
                status=404,
            )

        received_hmac = request.httprequest.headers.get('X-Shopify-Hmac-Sha256')
        if not received_hmac:
            _logger.warning("Missing Shopify HMAC header for account %s", account_id)
            return self._json_response(
                {'success': False, 'message': 'Missing Shopify HMAC header'},
                status=401,
            )

        raw_payload = request.httprequest.data or b''
        secret = (account.shopify_client_secret or '').encode('utf-8')
        calculated_hmac = base64.b64encode(
            hmac.new(secret, raw_payload, hashlib.sha256).digest()
        ).decode('utf-8')
        _logger.info("Incoming Shopify HMAC for account %s: %s", account_id, received_hmac)
        _logger.info("Calculated Shopify HMAC for account %s: %s", account_id, calculated_hmac)

        if not hmac.compare_digest(calculated_hmac, received_hmac):
            _logger.warning("Invalid Shopify HMAC for account %s", account_id)
            return self._json_response(
                {'success': False, 'message': 'Invalid Shopify HMAC'},
                status=401,
            )

        request.shopify_webhook_account = account
        return handler(self, account_id, *args, **kwargs)
    return wrapper


class ShopifyUpsyncWebhookController(http.Controller):

    @staticmethod
    def _extract_shopify_order_id(payload):
        admin_graphql_api_id = payload.get('admin_graphql_api_id') or ''
        if admin_graphql_api_id.startswith('gid://shopify/Order/'):
            return admin_graphql_api_id.rsplit('/', 1)[-1]
        order_id = payload.get('id')
        if order_id:
            return str(order_id)
        return None

    @http.route(
        [
            '/api/v1/order/create/account/<int:account_id>',
            '/odoo/api/v1/order/create/account/<int:account_id>',
            '/api/v1/order/update/account/<int:account_id>',
            '/odoo/api/v1/order/update/account/<int:account_id>',
        ],
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    @verify_shopify_webhook_hmac
    def shopify_order_create_webhook(self, account_id, **kwargs):
        try:
            headers = dict(request.httprequest.headers)
            raw_payload = request.httprequest.data.decode('utf-8', errors='replace') if request.httprequest.data else ''
            _logger.info("Shopify webhook headers for account %s: %s", account_id, headers)
            _logger.info("Shopify webhook payload for account %s: %s", account_id, raw_payload)

            account = request.shopify_webhook_account
            payload = json.loads(raw_payload) if raw_payload else {}
            ecommerce_order_ref = self._extract_shopify_order_id(payload)
            if ecommerce_order_ref:
                account._sync_order_by_reference(ecommerce_order_ref)
            else:
                account.action_sync_orders()
            return self._json_response({
                'success': True,
                'message': 'Success',
            })
        except Exception as error:
            _logger.exception("Unexpected error while processing Shopify order webhook for account %s", account_id)
            return self._json_response(
                {'success': False, 'message': str(error)},
                status=500,
            )

    # === Product Metafield Webhooks === #

    @http.route(
        [
            '/api/v1/product/update/account/<int:account_id>',
            '/odoo/api/v1/product/update/account/<int:account_id>',
            '/api/v1/product/create/account/<int:account_id>',
            '/odoo/api/v1/product/create/account/<int:account_id>',
        ],
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    @verify_shopify_webhook_hmac
    def shopify_product_update_webhook(self, account_id, **kwargs):
        """Handle a Shopify products/create or products/update webhook.

        Does NOT trust the payload to contain the full metafield data — always
        re-fetches metafields from Shopify via the Admin GraphQL API.
        """
        try:
            raw_payload = request.httprequest.data.decode('utf-8', errors='replace') if request.httprequest.data else ''
            account = request.shopify_webhook_account
            payload = json.loads(raw_payload) if raw_payload else {}

            # Determine operation from the webhook topic header if present
            topic = dict(request.httprequest.headers).get('X-Shopify-Topic', '')
            operation = 'delete' if 'delete' in topic else 'create' if 'create' in topic else 'update'

            account._sync_shopify_metafields_from_webhook(
                {**payload, 'operation': operation},
            )
            return self._json_response({
                'success': True,
                'message': 'Product metafields synced',
            })
        except Exception as error:
            _logger.exception(
                "Unexpected error while processing Shopify product webhook for account %s",
                account_id,
            )
            return self._json_response(
                {'success': False, 'message': str(error)},
                status=500,
            )

    @http.route(
        [
            '/api/v1/product/delete/account/<int:account_id>',
            '/odoo/api/v1/product/delete/account/<int:account_id>',
        ],
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    @verify_shopify_webhook_hmac
    def shopify_product_delete_webhook(self, account_id, **kwargs):
        """Handle a Shopify products/delete webhook: archive any Odoo metafield
        rows that had previously been synced to/from the deleted product."""
        try:
            raw_payload = request.httprequest.data.decode('utf-8', errors='replace') if request.httprequest.data else ''
            account = request.shopify_webhook_account
            payload = json.loads(raw_payload) if raw_payload else {}
            account._sync_shopify_metafields_from_webhook(
                {**payload, 'operation': 'delete'},
            )
            return self._json_response({
                'success': True,
                'message': 'Product metafields archived',
            })
        except Exception as error:
            _logger.exception(
                "Unexpected error while processing Shopify product delete webhook for account %s",
                account_id,
            )
            return self._json_response(
                {'success': False, 'message': str(error)},
                status=500,
            )

    def _json_response(self, data, status=200):
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )
