# Part of Odoo. See LICENSE file for full copyright and licensing details.
import itertools
import logging

import dateutil.parser
import psycopg2

from odoo import SUPERUSER_ID, Command, api, fields, models, modules
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.modules.registry import Registry
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import groupby

from odoo.addons.odoo_ecommerce.utils import (
    ECommerceApiError,
    ensure_account_is_authenticated,
    fetch_image_base64_from_url,
)

_logger = logging.getLogger(__name__)


class ECommerceAccount(models.Model):
    _name = 'ecommerce.account'
    _description = "E-commerce Account"
    _check_company_auto = True

    name = fields.Char(
        required=True,
    )
    active = fields.Boolean(
        help="If made inactive, this account will no longer be synchronized with E-commerce Platform.",
        default=True,
    )

    # channel fields
    ecommerce_channel_id = fields.Many2one(
        comodel_name='ecommerce.channel',
        string="E-commerce Channel",
        required=True,
    )
    channel_code = fields.Char(
        related='ecommerce_channel_id.code',
    )

    # support features fields.
    support_location = fields.Boolean(
        related='ecommerce_channel_id.support_location',
    )
    support_shipping = fields.Boolean(
        related='ecommerce_channel_id.support_shipping',
    )
    # configuration fields
    company_id = fields.Many2one(
        comodel_name='res.company',
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    fulfilled_by = fields.Selection(
        string="Delivery Handled On",
        selection=[
            ('odoo', "Odoo"),
            ('ecommerce', "E-commerce Platform"),
        ],
        required=True,
        default='ecommerce',
    )
    location_id = fields.Many2one(
        string="Stock Location",
        help="The location of the stock managed by E-commerce Platform",
        comodel_name='stock.location',
        domain='[("usage", "=", "internal")]',
        check_company=True,
    )
    payment_journal_id = fields.Many2one(
        comodel_name='account.journal',
        string="Payment Journal",
        domain=[('type', 'in', ('bank', 'cash'))],
        check_company=True,
        help="Journal used to register payments for synced orders.",
    )
    account_journal_id = fields.Many2one(
        comodel_name='account.journal',
        string="Sales Journal",
        domain=[('type', '=', 'sale')],
        check_company=True,
        help="Journal used when creating sales orders for synced orders.",
    )
    team_id = fields.Many2one(
        comodel_name='crm.team',
        string="Sales Team",
        check_company=True,
    )
    user_id = fields.Many2one(
        comodel_name='res.users',
        string="Salesperson",
        default=lambda self: self.env.user,
        check_company=True,
    )
    auto_create_product = fields.Boolean(
        string="Create Products",
        help="Automatically create products in Odoo when importing orders from E-Commerce."
    )
    update_inventory = fields.Boolean(
        string="Update Inventory",
        help="When enabled, this option allows the inventory to be updated on the e-commerce platform.",
        default=True,
    )
    tax_included = fields.Boolean(
        string="Tax Included",
        help="Whether the price includes the tax amount or not.",
    )  # TODO: Remove this field since tax inclusion depends only on the order.
    auto_create_invoice = fields.Boolean(
        string="Create Invoice",
        help="Automatically create an invoice in Odoo when importing paid orders from E-Commerce.",
        default=True,
    )

    # Display fields.
    state = fields.Selection(
        selection=[
            ('connected', "Connected"),
            ('disconnected', "Disconnected"),
        ],
        string="State",
        default='disconnected',
    )
    ecommerce_offer_ids = fields.One2many(
        comodel_name='ecommerce.offer',
        string="E-commerce Offers",
        inverse_name='ecommerce_account_id',
        bypass_search_access=True,
    )
    default_product_ids = fields.Many2many(
        string="Default Products",
        comodel_name='product.product',
        compute='_compute_default_product_ids',
        context={'active_test': False},
    )
    sale_order_ids = fields.One2many(
        comodel_name='sale.order',
        string="Sale Orders",
        inverse_name='ecommerce_account_id',
    )
    ecommerce_location_ids = fields.One2many(
        comodel_name='ecommerce.location',
        string="E-commerce Locations",
        inverse_name='ecommerce_account_id',
        bypass_search_access=True,
    )

    # last sync date fields.
    last_products_sync = fields.Datetime(
        string="Last Products Sync",
        help="The last time products were synced with E-commerce Platform.",
        required=True,
        default=fields.Datetime.now,
    )
    last_orders_sync = fields.Datetime(
        string="Last Orders Sync",
        help="The last time orders were synced with E-commerce Platform.",
        required=True,
        default=fields.Datetime.now,
    )

    # count fields.
    offer_count = fields.Integer(string="E-commerce Offer Count", compute='_compute_offer_count')
    order_count = fields.Integer(string="E-commerce Order Count", compute='_compute_order_count')
    location_count = fields.Integer(string="E-commerce Location Count", compute='_compute_location_count')

    _unique_name = models.Constraint(
        'UNIQUE(company_id, name)',
        "The name must be unique within the same company",
    )

    # === ORM METHODS ===#

    def _valid_field_parameter(self, field, name):
        return name == 'required_if_channel' or super()._valid_field_parameter(field, name)

    # === COMPUTE METHODS ===#

    def _compute_default_product_ids(self):
        ProductProduct = self.env['product.product']
        default_products = (
            self.env.ref('odoo_ecommerce.ecommerce_default_product', raise_if_not_found=False)
            or ProductProduct._restore_data_product("E-commerce Sales", 'consu', 'ecommerce_default_product')
        )
        for product_type in ['adjustment', 'discount', 'shipping']:
            default_products += (
                self.env.ref(f'odoo_ecommerce.ecommerce_default_{product_type}', raise_if_not_found=False)
                or ProductProduct._restore_data_product(f"E-commerce {product_type.capitalize()}", 'service', f'ecommerce_default_{product_type}')
            )
        for account in self:
            account.default_product_ids = default_products

    @api.depends('ecommerce_offer_ids')
    def _compute_offer_count(self):
        offers_data = self.env['ecommerce.offer']._read_group(
            [('ecommerce_account_id', 'in', self.ids)],
            groupby=['ecommerce_account_id'],
            aggregates=['__count'],
        )
        accounts_data = {account.id: count for account, count in offers_data}
        for account in self:
            account.offer_count = accounts_data.get(account.id, 0)

    @api.depends('sale_order_ids')
    def _compute_order_count(self):
        orders_data = self.env['sale.order']._read_group(
            [('ecommerce_account_id', 'in', self.ids)],
            groupby=['ecommerce_account_id'],
            aggregates=['__count'],
        )
        orders_per_account = {account.id: count for account, count in orders_data}
        for account in self:
            account.order_count = orders_per_account.get(account.id, 0)

    @api.depends('ecommerce_location_ids')
    def _compute_location_count(self):
        location_data = self.env['ecommerce.location']._read_group(
            [('ecommerce_account_id', 'in', self.ids)],
            groupby=['ecommerce_account_id'],
            aggregates=['__count'],
        )
        locations_per_account = {account.id: count for account, count in location_data}
        for account in self:
            account.location_count = locations_per_account.get(account.id, 0)

    # === ONCHANGE METHODS === #

    @api.onchange('last_orders_sync')
    def _onchange_last_orders_sync(self):
        """ Display a warning about the possible consequences of modifying the last orders sync. """
        self.ensure_one()
        if self._origin.id:
            return {
                'warning': {
                    'title': self.env._("Warning"),
                    'message': self.env._(
                        "If the date is set in the past, orders placed on this %s "
                        "Account before the first synchronization of the module might be "
                        "synchronized with Odoo.\n"
                        "If the date is set in the future, orders placed on this %s "
                        "Account between the previous and the new date will not be "
                        "synchronized with Odoo.",
                        self.ecommerce_channel_id.name, self.ecommerce_channel_id.name
                    ),
                },
            }

    @api.onchange('last_products_sync')
    def _onchange_last_products_sync(self):
        """ Display a warning about the possible consequences of modifying the last products sync. """
        self.ensure_one()
        if self._origin.id:
            return {
                'warning': {
                    'title': self.env._("Warning"),
                    'message': self.env._(
                        "If the date is set in the past, products created on this %s "
                        "Account before the first synchronization of the module might be "
                        "synchronized with Odoo.\n"
                        "If the date is set in the future, products created on this %s "
                        "Account between the previous and the new date will not be "
                        "synchronized with Odoo.",
                        self.ecommerce_channel_id.name, self.ecommerce_channel_id.name
                    ),
                },
            }

    # === CRUD METHODS ===#

    @api.model_create_multi
    def create(self, vals_list):
        vals_grouped_by_channel = {}
        for vals in vals_list:
            channel_id = vals.get('ecommerce_channel_id')
            vals_grouped_by_channel.setdefault(channel_id, []).append(vals)

        for channel_id, grouped_vals_list in vals_grouped_by_channel.items():
            ecommerce_accounts_rg = self._read_group([('ecommerce_channel_id', '=', channel_id)], ['team_id', 'location_id'])
            ecommerce_teams_ids = [team.id for team, __ in ecommerce_accounts_rg]
            ecommerce_locations_ids = [location.id for __, location in ecommerce_accounts_rg]
            ecommerce_channel_name = self.env['ecommerce.channel'].browse(channel_id).name
            for vals in grouped_vals_list:
                # Find or create the location of the Ecommerce location to be associated with this account
                location = self.env['stock.location'].search([
                    *self.env['stock.location']._check_company_domain(vals.get('company_id')),
                    ('id', 'in', ecommerce_locations_ids),
                ], limit=1)
                if not location:
                    parent_location_data = self.env['stock.warehouse'].search_read(
                        [*self.env['stock.warehouse']._check_company_domain(self.env.company)],
                        ['view_location_id'],
                        limit=1,
                    )
                    location = self.env['stock.location'].create({
                        'name': vals.get('name') or ecommerce_channel_name,
                        'usage': 'internal',
                        'location_id': parent_location_data[0]['view_location_id'][0] if parent_location_data else False,
                        'company_id': vals.get('company_id'),
                    })
                vals.update({'location_id': location.id})

                # Find or create the sales team to be associated with this account
                team = self.env['crm.team'].search([
                    *self.env['crm.team']._check_company_domain(vals.get('company_id')),
                    ('id', 'in', ecommerce_teams_ids),
                ], limit=1)
                if not team:
                    team = self.env['crm.team'].create({
                        'name': vals.get('name') or ecommerce_channel_name,
                        'company_id': vals.get('company_id'),
                    })
                vals.update({'team_id': team.id})
        flat_vals_list = [vals for grouped_vals in vals_grouped_by_channel.values() for vals in grouped_vals]
        accounts = super().create(flat_vals_list)
        accounts._check_required_if_channel()
        return accounts

    def write(self, vals):
        result = super().write(vals)
        self._check_required_if_channel()
        return result

    def copy_data(self, default=None):
        default = dict(default or {})
        vals_list = super().copy_data(default=default)
        for ecommerce_account, vals in zip(self, vals_list):
            if 'name' not in default:
                vals['name'] = self.env._("%s (copy)", ecommerce_account.name)
        return vals_list

    def log_xml(self, message, func, type='client', level='Error', name=''):
        self.env.flush_all()
        db_name = self.env.cr.dbname
        try:
            with Registry(db_name).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                IrLogging = env['ir.logging']
                IrLogging.sudo().create({
                    'name': name if name else f"{self.name}-{self.id}",
                    'type': type,
                    'dbname': db_name,
                    'level': level,
                    'message': message,
                    'path': "E-Commerce",
                    'func': func,
                    'line': 1,
                })
        except psycopg2.Error:
            pass

    # === ACTION METHODS ===#

    def action_archive(self):
        """Override to disconnect the E-commerce account before archiving it."""
        self.action_disconnect()
        return super().action_archive()

    # connect to the ecommerce platform
    def action_connect(self):
        self.state = 'connected'
        return True

    # disconnect from the ecommerce platform
    def action_disconnect(self):
        self.state = 'disconnected'
        return True

    @api.model
    def action_open_ecommerce_accounts(self, channel_code):
        ecommerce_channel = self.env.ref(f'ecommerce_{channel_code}.ecommerce_channel_{channel_code}')
        accounts = self.env['ecommerce.account'].search([('channel_code', '=', channel_code)])
        action_open_ecommerce = {
            'type': 'ir.actions.act_window',
            'name': f"{ecommerce_channel.name} Accounts",
            'res_model': 'ecommerce.account',
            'context': {
                'default_ecommerce_channel_id': ecommerce_channel.id,
                'default_name': ecommerce_channel.name,
            },
        }
        if not accounts:
            action_open_ecommerce.update({
                'view_mode': 'form',
                'target': 'current',
            })
        else:
            action_open_ecommerce.update({
                'view_mode': 'list,form',
                'domain': [('channel_code', '=', channel_code)],
            })
        return action_open_ecommerce

    def action_view_ecommerce_offers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Offers"),
            'res_model': 'ecommerce.offer',
            'view_mode': 'list',
            'domain': [('ecommerce_account_id', '=', self.id)],
            'context': {'default_ecommerce_account_id': self.id},
        }

    def action_view_ecommerce_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Orders"),
            'res_model': 'sale.order',
            'view_mode': 'list,form',
            'domain': [('ecommerce_account_id', '=', self.id)],
            'context': {'create': False},
        }

    def action_view_ecommerce_location(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Locations"),
            'res_model': 'ecommerce.location',
            'view_mode': 'list,form',
            'domain': [('ecommerce_account_id', '=', self.id)],
            'context': {'default_ecommerce_account_id': self.id},
        }

    def action_view_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Logs"),
            'res_model': 'ir.logging',
            'view_mode': 'list,form',
            'domain': [('name', '=', f"{self.name}-{self.id}")],
            'context': {'create': False},
        }

    def action_sync_products(self):
        return self._sync_products()

    def action_sync_orders(self):
        return self._sync_orders()

    def action_sync_locations(self):
        return self._sync_locations()

    def action_sync_inventory(self):
        return self._sync_inventory()

    def action_update_pickings(self):
        return self._update_pickings()

    def action_update_inventory(self):
        return self._update_inventory()

    def action_ecommerce_recover_order(self):
        self.ensure_one()
        return {
            'name': self.env._("Recover Order"),
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'ecommerce.recover.order.wizard',
            'target': 'new',
        }

    # === SETUP METHODS === #

    def _check_required_if_channel(self):
        """Check that channel-specific required fields have been filled.

        The fields that have the `required_if_channel='<channel_code>'` attribute are made
        required for all `ecommerce.account` records with the `channel_code` field equal to
        `<channel_code>` and with the `state` field equal to `'connected'`.

        Channel-specific views should make the form fields required under the same conditions.

        :return: None
        :raise ValidationError: If a channel-specific required field is empty.
        """
        field_names = []
        for field_name, field in self._fields.items():
            required_for_channel_code = getattr(field, 'required_if_channel', None)
            if required_for_channel_code and any(
                required_for_channel_code == account.channel_code and not account[field_name]
                for account in self
            ):
                ir_field = self.env['ir.model.fields']._get(self._name, field_name)
                field_names.append(ir_field.field_description)
        if field_names:
            raise ValidationError(self.env._("The following fields must be filled: %s", ", ".join(field_names)))

    # === SYNC METHODS === #

    def _sync_products(self, auto_commit=True):
        """
        We commit the changes after each offer is successfully processed.
        This allows us to retain the processed offers if an error occurs in any offer or in any account.
        """
        count_fetched = 0
        count_processed = 0
        count_failed = 0
        for account in self:
            try:
                ensure_account_is_authenticated(account)
                result = account._fetch_products_from_ecommerce()
            except (ECommerceApiError, UserError) as error:
                account.log_xml(
                    "An error occurred while fetching products for %s account with id %s.\n"
                    "Error description: %s" %
                    (account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                    '_sync_products',
                    'server',
                )
                continue  # skip this account and continue with the next one
            products_data = result.get('products', [])
            count_fetched += len(products_data)
            for product_data in products_data:
                try:
                    processed_offer = None
                    if auto_commit:
                        with self.env.cr.savepoint():
                            processed_offer = account._find_or_create_offer(product_data, auto_match=True)
                    else:  # Avoid the savepoint in testing
                        processed_offer = account._find_or_create_offer(product_data, auto_match=True)
                    if processed_offer:
                        count_processed += 1
                except Exception as error:
                    if modules.module.current_test:
                        raise  # we are executing during testing, do not try to rollback
                    if isinstance(error, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
                        account.log_xml(
                            "A concurrency error occurred while processing the offer data "
                            "with ec_product_identifier %s for %s account with id %s.\n"
                            "Error description: %s" %
                            (product_data.get('ec_product_identifier'), account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                            '_sync_products',
                        )
                        raise
                    account.log_xml(
                        "Error occurred while processing the product data "
                        "with ec_product_identifier '%s' for %s account with id '%s'.\n"
                        "Error description: %s" %
                        (product_data.get('ec_product_identifier'), account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                        '_sync_products',
                    )
                    self.env.cr.rollback()
                    count_failed += 1
                if auto_commit:
                    self.env.cr.commit()
            account.last_products_sync = fields.Datetime.now()
        message = "No products found."
        if count_fetched:
            message = f"Fetched: {count_fetched} | Processed: {count_processed} | Failed: {count_failed}"
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if count_failed else 'success',
                'title': self.env._("Product Sync Summary") if count_fetched else "",
                'message': self.env._(message),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _sync_orders(self, auto_commit=True):
        """Synchronize the account's sales orders that were recently updated on E-commerce Platform.

        Note: This method is called by the `ir_cron_ecommerce_sync_orders` cron.

        :param bool auto_commit: Whether the database cursor should be committed as soon as an order
                                 is successfully synchronized.
        :return: None
        """
        domain = Domain([
            ('active', '=', True),
            ('state', '=', 'connected'),
        ])
        accounts = self.filtered(domain) if self else self.search(domain)
        for account in accounts:
            if account.support_location:
                account.action_sync_locations()
        count_fetched = 0
        count_processed = 0
        count_failed = 0
        for account in accounts:
            account = account.with_prefetch()  # Avoid pre-fetching after each cache invalidation.
            try:
                ensure_account_is_authenticated(account)
                result = account._fetch_orders_from_ecommerce()
            except (ECommerceApiError, UserError) as error:
                account.log_xml(
                    "An error occurred while fetching orders for %s account with id %s.\n"
                    "Error description: %s" %
                    (account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                    '_sync_orders',
                    'server',
                )
                continue  # skip this account and continue with the next one
            orders_data = result if isinstance(result, list) else (result.get('orders') or [])
            # Also check existing imported orders for this account that are missing an invoice
            un_invoiced_orders = self.env['sale.order'].search([
                ('ecommerce_account_id', '=', account.id),
                ('ecommerce_order_identifier', '!=', False),
                ('state', '!=', 'cancel'),
            ], order='id desc', limit=50)
            un_invoiced_orders = un_invoiced_orders.filtered(
                lambda so: not so.invoice_ids.filtered(lambda inv: inv.state != 'cancel')
            )
            synced_ids = {str(d.get('id')) for d in orders_data}
            for un_invoiced_order in un_invoiced_orders:
                if str(un_invoiced_order.ecommerce_order_identifier) in synced_ids:
                    continue
                try:
                    order_data = account._fetch_order_from_ecommerce_by_order_ref(
                        un_invoiced_order.ecommerce_order_identifier
                    )
                    if order_data:
                        orders_data.append(order_data)
                        synced_ids.add(str(un_invoiced_order.ecommerce_order_identifier))
                except Exception as ex:
                    _logger.warning("Could not fetch order %s for invoice check: %s",
                                   un_invoiced_order.ecommerce_order_identifier, ex)
            count_fetched += len(orders_data)
            for order_data in orders_data:
                try:
                    processed_order = None
                    if auto_commit:
                        with self.env.cr.savepoint():
                            processed_order = account._process_order_data(order_data)
                    else:  # Avoid the savepoint in testing
                        processed_order = account._process_order_data(order_data)
                        
                    if processed_order:
                        count_processed += 1
                except Exception as error:
                    if modules.module.current_test:
                        raise  # we are executing during testing, do not try to rollback
                    if isinstance(error, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
                        account.log_xml(
                            "A concurrency error occurred while processing the order data "
                            "with ec_order_identifier %s for %s account with id %s.\n"
                            "Error description: %s" %
                            (order_data.get('id'), account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                            '_sync_orders',
                        )
                        raise
                    self.env.cr.rollback()
                    account._handle_sync_failure(
                        flow='order_sync', data={'ec_order_ref': order_data.get('reference')}, error_messages=str(error).split('DETAIL')[0],
                    )
                    account.log_xml(
                        "Error occurred while processing the order data "
                        "with ec_order_identifier %s for %s account with id %s.\n"
                        "Error description: %s" %
                        (order_data.get("id"), account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                        '_sync_orders',
                        'server',
                    )
                    count_failed += 1
                    continue  # Skip these order data and resume with the next ones.
                if auto_commit:
                    self.env.cr.commit()
            account.last_orders_sync = fields.Datetime.now()
        message = "No orders found."
        if count_fetched:
            message = f"Fetched: {count_fetched} | Processed: {count_processed} | Failed: {count_failed} | Not confirmed: {count_fetched - count_processed - count_failed}"
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if count_failed else 'success',
                'title': self.env._("Order Sync Summary") if count_fetched else "",
                'message': self.env._(message),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _sync_order_by_reference(self, ecommerce_order_ref):
        ensure_account_is_authenticated(self)
        try:
            result = self._fetch_order_from_ecommerce_by_order_ref(ecommerce_order_ref)
        except ECommerceApiError as error:
            raise UserError(self.env._("Error during fetching orders from %s account: %s", self.name, str(error).split('DETAIL')[0])) from error
        self._process_order_data(result)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': self.env._("Order Sync:"),
                'message': self.env._("Order with reference %s was fetched and processed successfully.", result.get('id')),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _sync_locations(self, auto_commit=True):
        """
        We commit the changes after each location is successfully processed.
        This allows us to retain the processed location if an error occurs in any location or in any account.
        """
        count_fetched = 0
        count_processed = 0
        count_failed = 0
        for account in self:
            try:
                ensure_account_is_authenticated(account)
                result = account._fetch_locations_from_ecommerce()
            except (ECommerceApiError, UserError) as error:
                account.log_xml(
                    "An error occurred while fetching locations for %s account with id %s.\n"
                    "Error description: %s" %
                    (account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                    '_sync_locations',
                    'server',
                )
                continue  # skip this account and continue with the next one
            locations_data = result.get('locations', [])
            count_fetched += len(locations_data)
            for location_data in locations_data:
                try:
                    proccessed_location = None
                    if auto_commit:
                        with self.env.cr.savepoint():
                            proccessed_location = account._find_or_create_location(location_data.get('id'), location_data.get('name'))
                    else:  # Avoid the savepoint in testing
                        proccessed_location = account._find_or_create_location(location_data.get('id'), location_data.get('name'))
                    if proccessed_location:
                        count_processed += 1
                except Exception as error:
                    if modules.module.current_test:
                        raise  # we are executing during testing, do not try to rollback
                    if isinstance(error, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
                        account.log_xml(
                            "A concurrency error occurred while processing the location data "
                            "with id %s for %s account with id %s.\n"
                            "Error description: %s" %
                            (location_data.get('id'), account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                            '_sync_locations',
                        )
                        raise
                    account.log_xml(
                        "Error occurred while processing the location data "
                        "with id '%s' for %s account with id '%s'.\n"
                        "Error description: %s" %
                        (location_data.get('id'), account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                        '_sync_locations',
                    )
                    self.env.cr.rollback()
                    count_failed += 1
                if auto_commit:
                    self.env.cr.commit()
        message = "No locations found."
        if count_fetched:
            message = f"Fetched: {count_fetched} | Processed: {count_processed} | Failed: {count_failed}"
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if count_failed else 'success',
                'title': self.env._("Location Sync Summary") if count_fetched else "",
                'message': self.env._(message),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _sync_inventory(self, auto_commit=True):
        """Pull inventory from the E-commerce platform and assign it to the corresponding location.

        :param bool auto_commit: Whether the database cursor should be committed as soon as the
                                inventory for a location is successfully synchronized.
        :return: None
        """
        domain = Domain([
            ('active', '=', True),
            ('state', '=', 'connected'),
        ])
        accounts = self.filtered(domain) if self else self.search(domain)
        notification_type = 'success'
        for account in accounts:
            try:
                ensure_account_is_authenticated(account)
                result = account._fetch_inventory_from_ecommerce()
            except (ECommerceApiError, UserError) as error:
                account.log_xml(
                    "An error occurred while fetching inventory for %s account with id %s.\n"
                    "Error description: %s" %
                    (account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                    '_sync_inventory',
                    'server',
                )
                continue  # skip this account and continue with the next one
            for inventory_data in result.get('inventory', []):
                try:
                    if auto_commit:
                        with self.env.cr.savepoint():
                            account._process_inventory_data(inventory_data)
                    else:  # Avoid the savepoint in testing
                        account._process_inventory_data(inventory_data)
                except Exception as error:
                    if modules.module.current_test:
                        raise  # we are executing during testing, do not try to rollback
                    if isinstance(error, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
                        account.log_xml(
                            "A concurrency error occurred while processing the inventory data "
                            "for %s account with id %s.\n"
                            "Error description: %s" %
                            (account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                            '_sync_inventory',
                        )
                        raise
                    account.log_xml(
                        "Error occurred while processing the inventory data "
                        "for %s account with id %s.\n"
                        "Error description: %s" %
                        (account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                        '_sync_inventory',
                    )
                    notification_type = 'warning'
                    self.env.cr.rollback()
                if auto_commit:
                    self.env.cr.commit()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': notification_type,
                'message': self.env._("Inventory synced successfully." if notification_type == 'success' else "An error occurred while syncing inventory."),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _update_pickings(self):
        """Update the pickings created in case of fulfilled by odoo to the E-commerce platform.

        Note: This method is called by the `ir_cron_ecommerce_update_pickings` cron.

        :return: None
        """
        domain = Domain([
            ('active', '=', True),
            ('state', '=', 'connected'),
            ('fulfilled_by', '=', 'odoo'),
            ('support_shipping', '=', True),
        ])
        accounts = self.filtered(domain) if self else self.search(domain)
        count_total = 0
        count_success = 0
        count_failed = 0
        for account in accounts:
            pickings = self.env['stock.picking'].search([
                ('state', '=', 'done'),
                ('sale_id.ecommerce_account_id', '=', account.id),
                ('ecommerce_sync_status', '=', 'pending'),
                ('ecommerce_picking_identifier', '=', False),
            ])
            if not pickings:
                continue
            count_total += len(pickings)
            try:
                ensure_account_is_authenticated(account)
            except UserError:
                count_failed += len(pickings)
                account.log_xml(
                    "Skipping pickings update for the account with id %s because the account is not authenticated." % account.id,
                    '_update_pickings',
                )
                continue
            account._update_pickings_to_ecommerce(pickings)
            count_success += len(pickings.filtered(lambda d: d.ecommerce_sync_status == 'done'))
            count_failed += len(pickings.filtered(lambda d: d.ecommerce_sync_status == 'error'))
        message = "No pickings found for update."
        if count_total:
            message = f"Total: {count_total} | Updated: {count_success} | Failed: {count_failed}"
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if count_failed else 'success',
                'message': self.env._(message),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    @api.model
    def _update_pickings_to_ecommerce_via_pickings(self, pickings):
        """Update the pickings created when fulfillment is handled by Odoo to the E-commerce platform.

        Note: This method is called from `stock.picking`

        :return: None
        """
        count_total = 0
        count_success = 0
        count_failed = 0
        pickings = pickings.filtered(
            lambda picking: picking.sale_id and
            picking.ec_account_id and
            picking.ecommerce_sync_status != 'done',
        )
        accounts = groupby(pickings, key=lambda picking: picking.sale_id.ecommerce_account_id)
        for account, pickings in accounts:
            count_total += len(pickings)
            try:
                ensure_account_is_authenticated(account)
            except UserError:
                count_failed += len(pickings)
                account.log_xml(
                    "Skipping pickings update for the account with id %s because the account is not authenticated." % account.id,
                    '_update_pickings_to_ecommerce_via_pickings',
                )
                continue
            pickings_rs = self.env['stock.picking'].browse([d.id for d in pickings])
            account._update_pickings_to_ecommerce(pickings_rs)
            count_success += len(pickings_rs.filtered(lambda d: d.ecommerce_sync_status == 'done'))
            count_failed += len(pickings_rs.filtered(lambda d: d.ecommerce_sync_status == 'error'))
        message = "No pickings found for update."
        if count_total:
            message = f"Total: {count_total} | Updated: {count_success} | Failed: {count_failed}"
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if count_failed else 'success',
                'message': self.env._(message),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _update_inventory(self):
        domain = Domain([
            ('active', '=', True),
            ('state', '=', 'connected'),
            ('update_inventory', '=', True),
        ])
        accounts = self.filtered(domain) if self else self.search(domain)
        notification_type = 'success'
        for account in accounts:
            try:
                ensure_account_is_authenticated(account)
            except UserError:
                account.log_xml(
                    "Skipping pickings update for the account with id %s because the account is not authenticated." % account.id,
                    '_update_inventory',
                )
                continue  # skip this account and continue with the next one
            locations = account.ecommerce_location_ids.filtered(lambda location: location.sync_stock)
            offers = account.ecommerce_offer_ids.filtered(lambda offer: offer.sync_stock)
            inventory_data = []
            for offer in offers:
                if not account.support_location:
                    if account.location_id:
                        quantity = offer.matched_product_id.with_context(location=account.location_id.id).free_qty
                        inventory_data.append({
                            'offer': offer,
                            'quantity': quantity,
                        })
                    continue
                for location in locations:
                    quantity = offer.matched_product_id.with_context(location=location.matched_location_id.id).free_qty
                    inventory_data.append({
                        'offer': offer,
                        'location': location,
                        'quantity': quantity,
                    })
            if inventory_data:
                try:
                    account._update_inventory_to_ecommerce(inventory_data)
                except ECommerceApiError as error:
                    account._handle_sync_failure(
                        flow='inventory_update', error_messages=str(error).split('DETAIL')[0],
                    )
                    account.log_xml(
                        "Error occurred while updating inventory on %s account with id %s.\n"
                        "Error description: %s" %
                        (account.ecommerce_channel_id.name, account.id, str(error).split('DETAIL')[0]),
                        'update_inventory',
                        'server',
                    )
                    notification_type = 'warning'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': notification_type,
                'message': self.env._("Inventory successfully updated to the e-commerce platform." if notification_type == 'success' else "An error occurred while updating inventory on the e-commerce platform."),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    # === FIND OR CREATE METHODS === #

    def _find_or_create_offer(self, product_data, auto_match=True):
        """Find, update, or create an e-commerce offer.

        :param dict product_data: The product data to find & update or create the offer from.
        :param bool auto_match: Whether to automatically match the product based on SKU.
        :return: The ecommerce offer.
        :rtype: recordset of `ecommerce.offer`
        """
        self.ensure_one()
        product_data = dict(product_data or {})
        variant_data = product_data.pop('variant_data', None)
        template_identifier = product_data.get('ec_product_template_identifier')
        product_identifier = product_data.get('ec_product_identifier')
        offer_model = self.env['ecommerce.offer']

        # Shopify's variant ID is stable, unlike a SKU (which is optional and
        # can be edited).  Use the same identifier pair protected by the SQL
        # constraint so both a webhook and a manual sync reuse the same offer.
        identity_domain = [
            ('ecommerce_account_id', '=', self.id),
            ('ec_product_template_identifier', '=', template_identifier),
            ('ec_product_identifier', '=', product_identifier),
        ]
        offer = offer_model
        if template_identifier and product_identifier:
            offer = offer_model.search(identity_domain, limit=1)
        if not offer and product_data.get('sku'):
            # Compatibility for offers created before identifier-based lookup.
            offer = offer_model.search([
                ('ecommerce_account_id', '=', self.id),
                ('sku', '=', product_data['sku']),
            ], limit=1)

        if offer:
            # The persisted SKU must not be replaced during a retry: another
            # existing offer may already own a newly changed Shopify SKU.
            offer.write({key: value for key, value in product_data.items() if key != 'sku'})
            return offer

        matched_product = self._find_matching_product(
            product_data.get('sku') if auto_match else None,
            'ecommerce_default_product', "E-commerce Sales", 'consu',
        )
        if self.auto_create_product and matched_product.id == self.env.ref('odoo_ecommerce.ecommerce_default_product', raise_if_not_found=False).id:
            matched_product = self._create_product_from_data({
                **product_data,
                'variant_data': variant_data or {},
            })
        offer_values = {
            **product_data,
            'sku': product_data.get('sku') or None,
            'ecommerce_account_id': self.id,
            'matched_product_id': matched_product.id,
        }
        try:
            # A webhook and a manual sync can race after both perform the
            # lookup.  Roll back just the failed create, then reuse the offer
            # committed by the other request.
            with self.env.cr.savepoint():
                offer = offer_model.with_context(tracking_disable=True).create(offer_values)
        except psycopg2.IntegrityError:
            if not (template_identifier and product_identifier):
                raise
            offer = offer_model.search(identity_domain, limit=1)
            if not offer:
                raise
            offer.write({key: value for key, value in product_data.items() if key != 'sku'})
        return offer

    def _find_or_create_moves(self, order, order_location_id, fulfillments):
        """Generate a stock move for each product of the provided sales order.

        :param recordset order: The sales order to generate the stock moves for, as a `sale.order`
                                record.
        :param int order_location_id: record set of 'stock.location' used when no location is provided with the fulfillment.
        :param list fulfillments: E-commerce fulfillment details; stock moves are generated
                                  only for the products included in these fulfillments.
        :return: None
        """
        customers_location = self.env.ref('stock.stock_location_customers')
        reference = self.env['stock.reference'].search([
            ('sale_ids', 'in', order.id),
            ('name', '=', order.name),
        ])
        if not reference:
            reference = self.env['stock.reference'].create({
                'sale_ids': [Command.link(order.id)],
                'name': order.name,
            })
        for fulfillment in fulfillments:
            existing_picking = order.picking_ids.filtered(lambda r: r.ecommerce_picking_identifier == str(fulfillment.get('ecommerce_picking_identifier')))
            if existing_picking:
                if fulfillment.get('status') == 'canceled':
                    activity_message = self.env._(
                        "This delivery has been cancelled on %s, "
                        "please create a return for this delivery to adjust the stock.",
                        self.ecommerce_channel_id.name
                    )
                    existing_picking.activity_schedule(
                        act_type_xmlid='mail.mail_activity_data_todo',
                        user_id=self.user_id.id,
                        note=activity_message,
                    )
                continue
            if fulfillment.get('status') == 'canceled':
                continue
            line_items = fulfillment.get('line_items', [])
            fulfillment_mapped_lines = {
                str(line_item.get('ecommerce_line_identifier')): line_item for line_item in line_items
            }
            stock_moves = self.env['stock.move']
            stock_location_id = (
                self._find_or_create_location(fulfillment.get('location_id')).matched_location_id
                if fulfillment.get('location_id')
                else order_location_id
            )
            for order_line in order.order_line.filtered(
                lambda r: r.product_id.type != 'service' and not r.display_type,
            ):
                if fulfillment_mapped_lines.get(order_line.ecommerce_line_identifier):
                    picking_type_id = self.env['stock.picking.type'].search([
                        ('code', '=', 'outgoing'), ('warehouse_id', '=', order_line.warehouse_id.id),
                    ], limit=1)
                    stock_move = self.env['stock.move'].create({
                        'company_id': self.company_id.id,
                        'product_id': order_line.product_id.id,
                        'product_uom_qty': fulfillment_mapped_lines[order_line.ecommerce_line_identifier].get('quantity'),
                        'product_uom': order_line.product_uom_id.id,
                        'location_id': stock_location_id.id,
                        'location_dest_id': customers_location.id,
                        'state': 'draft',
                        'sale_line_id': order_line.id,
                        'reference_ids': [Command.link(stock_reference.id) for stock_reference in reference],
                        'picking_type_id': picking_type_id.id,
                        'ecommerce_move_identifier': fulfillment_mapped_lines[order_line.ecommerce_line_identifier].get('ecommerce_move_identifier'),
                    })
                    stock_move._action_confirm()
                    stock_move._action_assign()
                    stock_moves |= stock_move
            if stock_moves:
                shipping_code = fulfillment.get('carrier_id')
                shipping_product = self._find_matching_product(
                    shipping_code, 'ecommerce_default_shipping', "E-commerce Shipping", 'service',
                )
                tracking_ref = fulfillment.get('tracking_number')
                vals = {
                    'ecommerce_picking_identifier': str(fulfillment.get('ecommerce_picking_identifier')),
                }
                if tracking_ref:
                    vals.update({
                        'carrier_tracking_ref': tracking_ref,
                    })
                if shipping_code:
                    vals.update({
                        'carrier_id': self._find_or_create_delivery_carrier(shipping_code, shipping_product).id,
                    })
                stock_moves[0].picking_id.write(vals)
                stock_moves[0].picking_id.action_assign()
                stock_moves[0].picking_id.write({'partner_id': order.partner_shipping_id.id})
                # Set the partner_id to stock.picking seperatly to avoid changing the location_id
                # to its default location (see the _compute_location_id and _is_date_in_lock_period methods of stock.picking)

    def _find_or_create_delivery_carrier(self, shipping_code, shipping_product):
        """ Find or create a delivery carrier based on the shipping code.

        :param str shipping_code: The shipping code.
        :param record shipping_product: The shipping product matching the shipping code, as a
                                        `product.product` record.
        :return: The delivery carrier.
        :rtype: delivery.carrier
        """
        shipping_code = shipping_code.strip()
        delivery_method = self.env['delivery.carrier'].search(
            ['|', ('name', '=', shipping_code), ('delivery_type', '=', shipping_code)], limit=1,
        )
        if not delivery_method:
            delivery_method = self.env['delivery.carrier'].create({
                'name': shipping_code,
                'product_id': shipping_product.id,
                'is_create_from_ecommerce': True,
            })
        return delivery_method

    def _find_or_create_partner(self, address_data, address_type):
        """Find or create a partner based on the provided address data.
        The contact partner is searched based on all the personal information and only if the
        email is provided in address_data. A match thus only occurs if the customer had already made a
        previous order and if the personal information provided by the API did not change in the
        meantime. If there is no match, a new contact partner is created.

        :param dict address_data: The address data to find or create the partner from.
        :param str address_type: The type of the partner - 'contact', 'invoice', 'delivery', 'other'.
        :return: The found or created partner as a `res.partner` record.
        :rtype: recordset of `res.partner`
        """
        name = address_data.get('name')
        email = address_data.get('email')
        phone = address_data.get('phone')
        street = address_data.get('street')
        street2 = address_data.get('street2')
        zip_code = address_data.get('zip')
        city = address_data.get('city')
        state_code = address_data.get('state_code')
        country_code = address_data.get('country_code')
        country = self.env['res.country'].search([
            ('code', '=', country_code),
        ], limit=1)
        state = self.env['res.country.state'].search([
            ('country_id', '=', country.id),
            '|', ('code', '=ilike', state_code), ('name', '=ilike', state_code),
        ], limit=1)

        partner_vals = {
            'name': name or f"{self.ecommerce_channel_id.name} Customer # {address_data.get('customer_id')}",
            'email': email,
            'phone': phone,
            'street': street,
            'street2': street2,
            'zip': zip_code,
            'city': city,
            'state_id': state.id,
            'country_id': country.id,
            'customer_rank': 1,
            'type': address_type,
            'is_company': address_data.get('is_company'),
            'vat': address_data.get('vat'),
            'company_id': self.company_id.id,
        }

        # Search for an existing partner based on the personal information and email.
        partner = self.env['res.partner'].search([
            *self.env['res.partner']._check_company_domain(self.company_id),
            ('name', '=', name),
            ('email', '=', email),
        ], limit=1) if email else None
        if partner and not (
            partner.phone == phone
            and partner.street == street
            and (not partner.street2 or partner.street2 == street2)
            and partner.zip == zip_code
            and partner.city == city
            and partner.state_id.id == state.id
            and partner.country_id.id == country.id
        ):
            partner_vals.update({'parent_id': partner.id})
            partner = self.env['res.partner'].search([
                *self.env['res.partner']._check_company_domain(self.company_id),
                ('parent_id', '=', partner.id),
                ('type', '=', address_type),
                ('name', '=', name),
                ('street', '=', street),
                '|', ('street2', '=', False), ('street2', '=', street2),
                ('zip', '=', zip_code),
                ('city', '=', city),
                ('country_id', '=', country.id),
                ('state_id', '=', state.id),
            ], limit=1)
        if not partner:
            partner = self.env['res.partner'].with_context(tracking_disable=True).create(partner_vals)
        return partner

    def _find_or_create_partners_from_data(self, order_data):
        """Find or create the contact and delivery partners based on the provided customer data.

        :param dict order_data: The order data to find or create the partners from.
        :return: The contact and delivery partners, as `res.partner` records. When the contact
                 partner acts as delivery partner, the records are the same.
        :rtype: tuple[recordset of `res.partner`, recordset of `res.partner`]
        """

        billing_address = order_data.get('billing_address')
        shipping_address = order_data.get('shipping_address')
        billing_partner = self.env['res.partner']  # invoice_partner contact_partner
        shipping_partner = self.env['res.partner']  # delivery_partner

        is_billing_address_present = billing_address and any(billing_address.values())
        is_shipping_address_present = shipping_address and any(shipping_address.values())
        if not is_billing_address_present and not is_shipping_address_present:
            default_partner_name = f"{self.ecommerce_channel_id.name} Customer " + order_data.get('reference')
            default_partner = self.env['res.partner'].create({
                'name': default_partner_name,
            })
            return default_partner, default_partner

        billing_partner = self._find_or_create_partner(
            billing_address if is_billing_address_present else shipping_address, 'invoice')
        if not is_shipping_address_present:
            return billing_partner, billing_partner
        shipping_partner = self._find_or_create_partner(shipping_address, 'delivery')
        for other_address in order_data.get('other_addresses') or []:
            self._find_or_create_partner(other_address, 'other')

        # if partners have no state, despite receiving state code
        # then create an activity to set state because in case of
        # fulfillment by merchant state_code is needed.
        state_code = order_data.get('billing_address', {}).get('state_code')
        if billing_partner and state_code and not billing_partner.state_id:
            billing_partner._ecommerce_create_activity_set_state(self.user_id.id, state_code)
        state_code = order_data.get('shipping_address', {}).get('state_code')
        if shipping_partner and state_code and not shipping_partner.state_id:
            shipping_partner._ecommerce_create_activity_set_state(self.user_id.id, state_code)
        return billing_partner, shipping_partner

    def _find_or_create_pricelist(self, currency):
        """Find or create the pricelist based on the currency.

        :param recordset currency: The currency of the pricelist, as a `res.currency` record.
        :return: The pricelist.
        :rtype: recordset of `product.pricelist`
        """
        pricelist = self.env['product.pricelist'].with_context(active_test=False).search([
            *self.env['product.pricelist']._check_company_domain(self.company_id),
            ('currency_id', '=', currency.id),
        ], limit=1)
        if not pricelist:
            pricelist = self.env['product.pricelist'].with_context(tracking_disable=True).create({
                'name': f"E-commerce Pricelist {currency.name}",
                'active': False,
                'currency_id': currency.id,
                'company_id': self.company_id.id,
            })
        return pricelist

    def _find_or_create_location(self, identifier, name=None):
        """Find & update or create the ecommerce location based on the identifier of location.

        :param str identifier: The location identifier of ecommerce location.
        :param str name: Name of ecommerce location.
        :return: The ecommerce location.
        :rtype: recordset of `ecommerce.location`
        """
        location = self.ecommerce_location_ids.filtered(
            lambda location: location.ecommerce_location_identifier == str(identifier))
        if name and location and location.name != name:
            location.name = name
        elif not location:
            location = self.env['ecommerce.location'].create({
                'name': name,
                'ecommerce_location_identifier': identifier,
                'ecommerce_account_id': self.id,
                'matched_location_id': self.location_id.id,
            })
        return location

    def _find_or_create_payment_method(self, journal, payment_provider_code):
        """
        Find an existing inbound payment method line for the given journal and
        ecommerce payment provider code, or create one if it does not exist.

        :param journal: 'account.journal' record in which the payment method
            should exist.
        :param payment_provider_code: str code identifying the ecommerce payment
            provider (e.g., COD, Razorpay, Stripe).
        :return: 'account.payment.method.line' record corresponding to the
            provider for the given journal.
        :raises ValueError: If the manual inbound payment method is not found.
        """

        self.ensure_one()

        payment_method_line = self.env['account.payment.method.line']
        manual_method = self.env.ref('account.account_payment_method_manual_in', raise_if_not_found=True)

        method_line = payment_method_line.search([
            ('journal_id', '=', journal.id),
            ('payment_type', '=', 'inbound'),
            ('company_id', '=', self.company_id),
            ('ecommerce_payment_provider_code', '=', str(payment_provider_code).strip().lower()),
        ], limit=1)

        if method_line:
            return method_line

        return payment_method_line.create({
            'name': payment_provider_code,
            'payment_method_id': manual_method.id,
            'journal_id': journal.id,
            'payment_type': 'inbound',
            'company_id': self.company_id,
            'ecommerce_payment_provider_code': str(payment_provider_code).strip().lower(),
        })

    # === HELPER METHODS === #

    # FIXME: should this be on ecommerce.offer?
    def _get_product_url(self, offer):
        """Override this method to return the ecommerce's merchant portal product page URL.

        :rtype: str
        """
        return ""

    def _process_order_data(self, order_data):
        """Process the provided order data and return the matching sales order, if any.

        If no matching sales order is found, a new one is created if it is in a 'synchronizable' state.
        If the matching sales order already exists and the E-commerce order was canceled, the order is cancelled.

        :param dict order_data: The order data to process.
        :return: The matching ecommerce order, if any, as a `sale.order` record.
        :rtype: recordset of `sale.order`
        """
        self.ensure_one()

        ecommerce_order_identifier = order_data.get('id')
        order = self.sale_order_ids.filtered(lambda order:
            order.ecommerce_order_identifier == str(ecommerce_order_identifier))
        status = order_data.get('status') or 'confirmed'  # Default to `confirmed`
        fulfillments = order_data.get('fulfillments')
        if not order:  # order not found.
            if status == 'confirmed':
                order = self._create_order_from_data(order_data)
                if self.fulfilled_by == 'ecommerce' and fulfillments:
                    location_id = self._get_order_location(order_data)
                    self._find_or_create_moves(order, location_id, fulfillments)
                if self.fulfilled_by == 'ecommerce':
                    order.with_context(mail_notrack=True).action_lock()
                _logger.info(
                    "Created a new sales order with order reference %(ref)s for %(code)s account"
                    " with id %(id)s.", {'ref': ecommerce_order_identifier, 'code': self.channel_code, 'id': self.id},
                )
            else:
                self.log_xml(
                    message="Ignored order with reference %s because of status %s for %s account with id %s." %
                    (ecommerce_order_identifier, status, self.ecommerce_channel_id.name, self.id),
                    func='_process_order_data',
                    level='Info',
                )
        else:  # The sales order already exists
            if self.fulfilled_by == 'ecommerce' and fulfillments:
                location_id = self._get_order_location(order_data)
                self._find_or_create_moves(order, location_id, fulfillments)
            if status == 'canceled' and order.state != 'cancel':
                order.with_context(canceled_by_ecommerce=True)._action_cancel()
                _logger.info(
                    "Cancelled sales order with order reference %(ref)s for %(code)s account with id"
                    " %(id)s.", {'ref': ecommerce_order_identifier, 'code': self.channel_code, 'id': self.id},
                )
            else:
                _logger.info(
                    "Ignored already synchronized sales order with order reference %(ref)s for"
                    " ecommerce account with id %(id)s.", {'ref': ecommerce_order_identifier, 'id': self.id},
                )
        if self.fulfilled_by == 'odoo' and fulfillments:
            order._create_activity_resolve_fulfillment_conflict(self.user_id.id)

        if order:
            self._process_utm_data(order, order_data)

        if not self.auto_create_invoice or not order:
            return order

        try:
            self._auto_create_invoice_and_payment(order, order_data)
        except Exception as error:
            if isinstance(error, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
                raise  # Let the caller retry the whole order on a concurrency error.
            error_msg = str(error).split('DETAIL')[0].strip()
            raw_ref = order_data.get('reference') or order.ec_order_ref or order_data.get('id') or ''
            shopify_order_display = raw_ref if str(raw_ref).startswith('#') else f"#{raw_ref}"
            log_error = (
                f"Shopify Order {shopify_order_display}\n"
                f"Payment Status: {(order_data.get('financial_status') or 'PAID').strip().upper()}\n"
                f"Odoo Sales Order: {order.name}\n"
                f"Invoice Creation: FAILED\n"
                f"Error: {error_msg}"
            )
            _logger.error("%s", log_error)
            self.log_xml(
                message=log_error,
                func='_process_order_data',
                level='Error',
            )
            # Do not re-raise: the sales order was synchronized successfully and must be kept even
            # though the invoice could not be created. The next synchronization of this E-commerce
            # order will retry the invoice creation without creating a duplicate.
        return order

    def _create_order_from_data(self, order_data):
        """ Create a new sales order based on the provided order data.

        :param dict order_data: The order data to create a sales order from.
        :return: The newly created sales order.
        :rtype: record of `sale.order`
        """
        order_vals = self._prepare_order_values(order_data)
        order = self.env['sale.order'].with_context(
            mail_create_nosubscribe=True,
        ).with_company(self.company_id).create(order_vals)
        self._adjust_order_total(order, order_data)
        if self.fulfilled_by == 'odoo':
            order.action_confirm()
            order.picking_ids.filtered(
                lambda picking: picking.picking_type_code == 'outgoing'
                and picking.state in ('confirmed', 'waiting', 'partially_available')
            ).action_assign()
        return order

    def _prepare_order_values(self, order_data):
        """Prepare the values for the order to create based on E-commerce data.

        :param dict order_data: The order data to prepare sales order values from.
        :return: The order values.
        :rtype: dict
        """
        ecommerce_order_identifier = order_data.get('id')
        currency_code = order_data.get('currency_code')
        if currency_code:
            currency = self.env['res.currency'].with_context(active_test=False).search(
                [('name', '=', currency_code)], limit=1,
            )
            currency.write({'active': True}) #required while creating a payment with this currency, as inactive currencies cannot be used.
        else:
            currency = self.company_id.currency_id
        contact_partner, delivery_partner = self._find_or_create_partners_from_data(order_data)
        fiscal_position = self.env['account.fiscal.position'].with_company(
            self.company_id,
        )._get_fiscal_position(contact_partner, delivery_partner)
        order_lines_values = self._prepare_order_lines_values(
            order_data, currency, fiscal_position,
        )
        order_vals = {
            'origin': f"{self.ecommerce_channel_id.name} Order # {ecommerce_order_identifier}",
            'date_order': order_data.get('date_order') and dateutil.parser.parse(order_data.get('date_order')).replace(tzinfo=None) or None,  # default current date.
            'partner_id': contact_partner.id,
            'partner_shipping_id': delivery_partner.id,
            'pricelist_id': self._find_or_create_pricelist(currency).id,
            'order_line': [Command.create(order_line_values) for order_line_values in order_lines_values],
            'invoice_status': 'no',
            'require_signature': False,
            'require_payment': False,
            'fiscal_position_id': fiscal_position.id,
            'company_id': self.company_id.id,
            'user_id': self.user_id.id,
            'team_id': self.team_id.id,
            'ecommerce_order_identifier': ecommerce_order_identifier,
            'ec_order_ref': order_data.get('reference'),
            'ecommerce_account_id': self.id,
            'journal_id': self._get_ecommerce_account_journal().id,
        }
        if self.fulfilled_by == 'ecommerce':
            order_vals.update({
                'state': 'sale',
                'locked': self.support_shipping,  # Avoid creating pickings from action_lock for ecommerce-managed fulfillment.
            })
        order_vals['warehouse_id'] = self._get_order_location(order_data).warehouse_id.id

        return order_vals

    def _prepare_order_lines_values(self, order_data, currency, fiscal_pos):
        """Prepare the values for the order lines to create based on E-commerce data.

        :param dict order_data: The order data related to the item data.
        :param record currency: The currency of the sales order, as a `res.currency` record.
        :param record fiscal_pos: The fiscal position of the sales order, as an
                                  `account.fiscal.position` record.
        :return: The order lines values.
        :rtype: dict
        """
        order_lines_values = []
        lines_data = order_data.get('order_lines') or []
        for line_data in lines_data:
            offer = self._find_or_create_offer(line_data.get('product_data'))
            qty_ordered = float(line_data.get('qty_ordered') or 0.0)
            price_unit = float(line_data.get('price_unit') or 0.0)
            platform_subtotal = float(line_data.get('price_subtotal', price_unit * qty_ordered))
            tax_amount = float(line_data.get('tax_amount') or 0.0)
            product_taxes = offer.matched_product_id.taxes_id.filtered_domain(
                [*self.env['account.tax']._check_company_domain(self.company_id)],
            )
            taxes = fiscal_pos.map_tax(product_taxes) if fiscal_pos else product_taxes
            price_subtotal = platform_subtotal - tax_amount if order_data.get('tax_included') else platform_subtotal
            discount_subtotal = float(line_data.get('discount_amount', 0))
            
            # Match actual e-commerce tax with Odoo taxes to prevent stacking duplicate taxes
            base_for_tax = price_subtotal - discount_subtotal
            taxes = self._match_taxes_to_amount(taxes, tax_amount, base_for_tax, currency)
            
            subtotal = self._recompute_subtotal(
                base_for_tax, tax_amount, taxes, currency,
            )
            order_line_description = (
                offer.matched_product_template_id.display_name + "\n" +
                (line_data.get('description') or f"[{offer.sku}] {offer.name}")
            )  # description only after the newline ('\n') is visible in the UI.
            properties = line_data.get('properties') or []
            props_text = "\n".join(
                f"{p['key']}: {p['value']}"
                for p in properties
                if p.get('key') and p.get('value') not in (None, '')
            )
            ecommerce_price_unit = float(line_data.get('price_unit') or 0.0)
            odoo_price_unit = float((subtotal / qty_ordered) if qty_ordered else 0)
            if currency.compare_amounts(ecommerce_price_unit, odoo_price_unit) != 0 and not currency.is_zero(ecommerce_price_unit):
                order_line_description += f"\nOriginal Price: {currency.format(ecommerce_price_unit)}"

            order_lines_values.append({
                'name': order_line_description,
                'product_id': offer.matched_product_id.id,
                'product_uom_qty': qty_ordered,
                'price_unit': (subtotal / qty_ordered) if qty_ordered else 0,
                'tax_ids': [Command.link(tax.id) for tax in taxes],
                'display_type': line_data.get('display_type', False),
                'ecommerce_line_identifier': str(line_data.get('id')),
                'shopify_line_properties': props_text or False,
            })

        discount_lines = order_data.get('discount_lines') or []
        for discount_line in discount_lines:
            discount_product = self._find_matching_product(
                None, 'ecommerce_default_discount', "E-commerce Discount", 'service',
            )
            platform_price_unit = float(discount_line.get('price_unit') or 0.0)
            tax_amount = float(discount_line.get('tax_amount') or 0.0)
            product_taxes = discount_product.taxes_id.filtered_domain(
                [*self.env['account.tax']._check_company_domain(self.company_id)],
            )
            taxes = fiscal_pos.map_tax(product_taxes) if fiscal_pos else product_taxes
            price_unit = platform_price_unit - tax_amount if order_data.get('tax_included') else platform_price_unit
            
            taxes = self._match_taxes_to_amount(taxes, tax_amount, price_unit, currency)
            
            subtotal = self._recompute_subtotal(
                price_unit, tax_amount, taxes, currency,
            )
            discount_line_description = (
                discount_product.product_tmpl_id.display_name + "\n" +
                (discount_line.get('description') or f"[{self.ecommerce_channel_id.name}] Discount")
            )
            order_lines_values.append({
                'name': discount_line_description,
                'product_id': discount_product.id,
                'product_uom_qty': 1.0,
                'price_unit': (-1 * subtotal) if subtotal else 0,
                'tax_ids': [Command.link(tax.id) for tax in taxes],
            })

        shipping_lines = order_data.get('shipping_lines') or []
        for shipping_line in shipping_lines:
            shipping_code = shipping_line.get('shipping_code')
            shipping_product = self._find_matching_product(
                shipping_code, 'ecommerce_default_shipping', "E-commerce Shipping", 'service',
            )
            platform_price_unit = float(shipping_line.get('price_unit') or 0.0)
            tax_amount = float(shipping_line.get('tax_amount') or 0.0)
            product_taxes = shipping_product.taxes_id.filtered_domain(
                [*self.env['account.tax']._check_company_domain(self.company_id)],
            )
            taxes = fiscal_pos.map_tax(product_taxes) if fiscal_pos else product_taxes
            price_unit = platform_price_unit - tax_amount if order_data.get('tax_included') else platform_price_unit
            discount_subtotal = float(shipping_line.get('discount_amount', 0))
            
            base_for_tax = price_unit - discount_subtotal
            taxes = self._match_taxes_to_amount(taxes, tax_amount, base_for_tax, currency)
            
            subtotal = self._recompute_subtotal(
                base_for_tax, tax_amount, taxes, currency,
            )
            shipping_line_description = (
                shipping_product.product_tmpl_id.display_name + "\n" +
                (shipping_line.get('description') or f"[{shipping_code}] Shipping")
            )
            ecommerce_shipping_price_unit = float(shipping_line.get('price_unit') or 0.0)
            if currency.compare_amounts(ecommerce_shipping_price_unit, subtotal) != 0 and not currency.is_zero(ecommerce_shipping_price_unit):
                shipping_line_description += (f"\nOriginal Price: {currency.format(ecommerce_shipping_price_unit)}")

            order_lines_values.append({
                'name': shipping_line_description,
                'product_id': shipping_product.id,
                'product_uom_qty': 1.0,
                'price_unit': subtotal,
                'tax_ids': [Command.link(tax.id) for tax in taxes],
                'ecommerce_line_identifier': str(shipping_line.get('id')),
            })

        return order_lines_values

    @api.model
    def _match_taxes_to_amount(self, taxes, tax_amount, base_amount, currency):
        """Filter taxes to match the exact tax amount from the e-commerce platform.

        This prevents stacked/duplicate taxes (e.g., DU + AZ + FU all at 5%)
        when Shopify has already calculated the correct tax.

        :param recordset taxes: Candidate taxes from product/fiscal position.
        :param float tax_amount: The actual tax amount from the e-commerce platform.
        :param float base_amount: The base amount (subtotal before tax).
        :param recordset currency: Currency for rounding checks.
        :return: Filtered taxes that produce the correct tax amount.
        :rtype: recordset of `account.tax`
        """
        if not taxes or currency.is_zero(base_amount):
            return self.env['account.tax'].browse()

        if currency.is_zero(tax_amount):
            return self.env['account.tax'].browse()

        taxes_list = list(taxes)
        best_match = self.env['account.tax'].browse()
        min_diff = float('inf')

        for r in range(1, len(taxes_list) + 1):
            for combo in itertools.combinations(taxes_list, r):
                combo_taxes = self.env['account.tax'].concat(*combo)
                taxes_res = combo_taxes.compute_all(base_amount, currency=currency)
                calculated_tax = sum(t['amount'] for t in taxes_res['taxes'])
                diff = abs(calculated_tax - tax_amount)

                if diff < min_diff:
                    min_diff = diff
                    best_match = combo_taxes

                if diff < 0.01:
                    return combo_taxes

        return best_match if best_match else taxes

    @api.model
    def _recompute_subtotal(self, subtotal, tax_amount, taxes, currency):
        """Recompute the subtotal from the tax amount and the taxes.

        As it is not always possible to find the right tax record for a tax rate computed from the
        tax amount because of rounding errors or because of multiple taxes for a given rate, the
        taxes on the product are used instead.

        To achieve this, the subtotal is recomputed from the taxes for the total to match that of
        the order in E-commerce Platform. If the taxes used are not identical to that used by E-commerce Platform, the
        recomputed subtotal will differ from the original subtotal.

        :param float subtotal: The original subtotal to use for the computation of the base total.
        :param float tax_amount: The original tax amount to use for the computation of the base
                                 total.
        :param recordset taxes: The final taxes to use for the computation of the new subtotal, as
                                an `account.tax` recordset.
        :param recordset currency: The currency used by the rounding methods, as a `res.currency`
                                   record.
        :return: The new subtotal.
        :rtype: float
        """
        total = subtotal + tax_amount
        taxes_res = taxes.with_context(force_price_include=True).compute_all(
            total, currency=currency,
        )
        subtotal = taxes_res['total_excluded']
        for tax_res in taxes_res['taxes']:
            tax = self.env['account.tax'].browse(tax_res['id'])
            if tax.price_include:
                subtotal += tax_res['amount']
        return subtotal

    def _adjust_order_total(self, order, order_data):
        """Adjust the order total to reconcile with ecommerce total_amount if there is a rounding
        residue.

        Order-level reconciliation: compare Odoo's computed total with
        E-commerce's total_amount and append a tax-free adjustment line if
        there is a rounding residue.

        :param sale.order order: The order to adjust.
        :param dict order_data: The order data from order is created.
        """
        residue = float(order_data.get('total_amount')) - order.amount_total
        if not order.currency_id.is_zero(residue):
            adjustment_product = self._find_matching_product(
                '', 'ecommerce_default_adjustment', "E-commerce Adjustment", 'service',
            )
            adjustment_line_description = (
                adjustment_product.product_tmpl_id.display_name + "\n" +
                f"{self.ecommerce_channel_id.name} Amount Adjustment"
            )
            order.order_line.create({
                'order_id': order.id,
                'name': adjustment_line_description,
                'product_id': adjustment_product.id,
                'price_unit': residue,
                'tax_ids': [],
                'product_uom_qty': 1,
                'discount': 0,
            })
            activity_message = (
                "There is a discrepancy between the total amount of this order and Odoo's computed total, "
                "and this is handled via an adjustment line. Please review this order before creating the invoice."
            )
            order.activity_schedule(
                act_type_xmlid='mail.mail_activity_data_todo',
                user_id=self.user_id.id,
                note=activity_message,
            )

    def _get_order_location(self, order_data):
        """ Get location based on the provided order_data

        :param dict order_data: The order data related to the item data.
        :return: Stock Location
        :rtype: record of `stock.location`
        """
        if not self.ecommerce_channel_id.support_location:
            return self.location_id
        location_identifier = order_data.get('location_id')
        if not location_identifier:
            if len(self.ecommerce_location_ids) == 1:
                return self.ecommerce_location_ids.matched_location_id
            return self.location_id
        ecommerce_location = self._find_or_create_location(location_identifier)
        return ecommerce_location.matched_location_id

    def _create_product_from_data(self, product_data):
        """ Create a `product.product` record from eCommerce product data.

        :param dict product_data: The product data to create the product from.
        :return: The product created based on product_data.
        :rtype: recordset of `product.product`
        """
        variant_data = product_data.get('variant_data', {})
        product_payload = {
            'default_code': product_data.get('sku'),
            'is_storable': True,
            'list_price': variant_data.get('list_price') or 0.0,
            'name': product_data.get('name'),
            'standard_price': variant_data.get('standard_price') or 0.0,
            'type': 'consu',
            'volume': variant_data.get('volume') or 0.0,
            'weight': variant_data.get('weight') or 0.0,
        }
        if 'image_url' in variant_data and variant_data.get('image_url'):
            try:
                product_payload.update({
                    'image_1920': fetch_image_base64_from_url(variant_data.get('image_url')),
                })
            except ECommerceApiError as error:
                self.log_xml(
                    "Error occurred while fetching image of product %s on %s account with id %s.\n"
                    "Error description: %s" %
                    (product_data.get('name'), self.ecommerce_channel_id.name, self.id, str(error)),
                    '_create_product_from_data',
                    'server',
                )
        if 'barcode' in variant_data and variant_data.get('barcode'):
            product_payload.update({
                'barcode': variant_data.get('barcode'),
            })
        if 'is_storable' in variant_data:
            product_payload.update({
                'is_storable': variant_data.get('is_storable') or False,
            })
        product = self.env['product.product'].create(product_payload)
        self._update_product_variant_data(product, variant_data)
        return product

    def _update_product_variant_data(self, product, variant_data):
        """Update product variant-level fields from e-commerce data.

        Called after product creation and on subsequent syncs to keep
        price, barcode, weight, etc. in sync with the e-commerce platform.

        :param product: product.product record
        :param dict variant_data: variant data from e-commerce
        """
        if not product or not variant_data:
            return
        vals = {}
        template = product.product_tmpl_id
        if 'list_price' in variant_data:
            new_price = variant_data.get('list_price')
            if new_price and template.list_price != new_price:
                vals['list_price'] = new_price
        if 'standard_price' in variant_data:
            new_cost = variant_data.get('standard_price')
            if new_cost and template.standard_price != new_cost:
                vals['standard_price'] = new_cost
        if 'weight' in variant_data:
            new_weight = variant_data.get('weight')
            if new_weight is not None and template.weight != new_weight:
                vals['weight'] = new_weight
        if 'barcode' in variant_data:
            new_barcode = variant_data.get('barcode')
            if new_barcode is not None and product.barcode != new_barcode:
                product.barcode = new_barcode
        if vals:
            template.write(vals)

    def _find_matching_product(
        self, internal_reference, default_xmlid, default_name, default_type, fallback=True
    ):
        """ Find the matching product for a given internal reference.

        If no product is found for the given internal reference, we fall back on the default
        product. If the default product was deleted, we restore it.

        Note: self.ensure_one()

        :param str internal_reference: The internal reference of the product to be searched.
        :param str default_xmlid: The xmlid of the default product to use as fallback.
        :param str default_name: The name of the default product to use as fallback.
        :param str default_type: The product type of the default product to use as fallback.
        :param bool fallback: Whether we should fall back to the default product when no product
                              matching the provided internal reference is found.
        :return: The matching product.
        :rtype: recordset of `product.product`
        """
        product = self.env['product.product'].search([
            *self.env['product.product']._check_company_domain(self.company_id),
            ('default_code', '=', internal_reference),
        ], limit=1) if internal_reference else self.env['product.product']
        if not product and fallback:  # Fallback to the default product
            product = self.env.ref('odoo_ecommerce.%s' % default_xmlid, raise_if_not_found=False)
        if not product and fallback:  # Restore the default product if it was deleted
            product = self.env['product.product']._restore_data_product(
                default_name, default_type, default_xmlid,
            )
        return product

    def _auto_create_invoice_and_payment(self, order, order_data):
        """Auto-create invoice and register payment for paid ecommerce orders.

        This method is idempotent: it is safe to call it again on every synchronization of the
        same E-commerce order (manual re-sync, retry after a previous failure, or a later sync
        that observes the payment status change from pending to paid). It never creates more than
        one (non-cancelled) invoice per sales order:
        - If a posted invoice already exists, nothing is created; only the payment is registered
          if it is not yet done.
        - If a draft invoice already exists (e.g. a previous synchronization created it but failed
          before posting it), that invoice is posted instead of creating a new one.
        - Otherwise, a new invoice is created (confirming the sales order first if needed), posted,
          and its payment is registered.

        :param order: sale.order record
        :param order_data: dict with order data from ecommerce platform
        """
        self.ensure_one()
        financial_status = (order_data.get('financial_status') or '').strip().upper()
        raw_ref = order_data.get('reference') or order.ec_order_ref or order_data.get('id') or ''
        shopify_order_display = raw_ref if str(raw_ref).startswith('#') else f"#{raw_ref}"

        # Only a Shopify payment confirmed as PAID triggers invoicing. Pending, failed, voided and
        # partially-paid orders are intentionally left without an invoice.
        if financial_status != 'PAID':
            _logger.info(
                "Invoice creation skipped for Shopify order %s: payment status is %s (not PAID).",
                shopify_order_display, financial_status,
            )
            return

        # Idempotency guard: never create a second invoice for the same sales order. A cancelled
        # invoice does not count, so a re-synchronization can still invoice again in that case.
        invoice = order.invoice_ids.filtered(lambda move: move.state != 'cancel')[:1]

        if invoice and invoice.state == 'posted':
            log_exists = (
                f"Shopify Order {shopify_order_display}\n"
                f"Payment Status: PAID\n"
                f"Odoo Sales Order: {order.name}\n"
                f"Invoice: {invoice.name}\n"
                f"Invoice Status: Posted (Already Exists)"
            )
            _logger.info("%s", log_exists)
            self.log_xml(
                message=log_exists,
                func='_auto_create_invoice_and_payment',
                level='Info',
            )
            self._register_ecommerce_invoice_payment(invoice, order, order_data)
            return

        if not invoice:
            if order.state == 'cancel':
                _logger.warning("Invoice not auto-created for order %s: sales order %s is cancelled.", shopify_order_display, order.name)
                return

            if order.state != 'sale':
                order.action_confirm()

            log_creating = (
                f"Shopify Order {shopify_order_display}\n"
                f"Payment Status: PAID\n"
                f"Odoo Sales Order: {order.name}\n"
                f"Invoice Status: Creating"
            )
            _logger.info("%s", log_creating)
            self.log_xml(
                message=log_creating,
                func='_auto_create_invoice_and_payment',
                level='Info',
            )

            try:
                with self.env.cr.savepoint():
                    invoice = order.with_context(
                        invoice_date=(order_data.get('invoice_date') and dateutil.parser.parse(order_data.get('invoice_date')).replace(tzinfo=None)) or None,
                        ecommerce_auto_invoice=True,
                        raise_if_nothing_to_invoice=False,
                    )._create_invoices()
                    if not invoice:
                        raise UserError(self.env._("No invoiceable lines found on sales order %s.", order.name))
                    if invoice.state != 'posted':
                        invoice.action_post()
            except Exception as error:
                if isinstance(error, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
                    raise
                error_msg = str(error).split('DETAIL')[0].strip()
                log_error = (
                    f"Shopify Order {shopify_order_display}\n"
                    f"Payment Status: PAID\n"
                    f"Odoo Sales Order: {order.name}\n"
                    f"Invoice Creation: FAILED\n"
                    f"Error: {error_msg}"
                )
                _logger.error("%s", log_error)
                self.log_xml(
                    message=log_error,
                    func='_auto_create_invoice_and_payment',
                    level='Error',
                )
                return
        else:
            # An unposted draft invoice already exists
            log_draft = (
                f"Shopify Order {shopify_order_display}\n"
                f"Payment Status: PAID\n"
                f"Odoo Sales Order: {order.name}\n"
                f"Invoice Status: Creating"
            )
            _logger.info("%s", log_draft)
            self.log_xml(
                message=log_draft,
                func='_auto_create_invoice_and_payment',
                level='Info',
            )
            if invoice.state != 'posted':
                try:
                    with self.env.cr.savepoint():
                        invoice.action_post()
                except Exception as error:
                    if isinstance(error, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
                        raise
                    error_msg = str(error).split('DETAIL')[0].strip()
                    log_error = (
                        f"Shopify Order {shopify_order_display}\n"
                        f"Payment Status: PAID\n"
                        f"Odoo Sales Order: {order.name}\n"
                        f"Invoice Creation: FAILED\n"
                        f"Error: {error_msg}"
                    )
                    _logger.error("%s", log_error)
                    self.log_xml(
                        message=log_error,
                        func='_auto_create_invoice_and_payment',
                        level='Error',
                    )
                    return

        log_posted = (
            f"Shopify Order {shopify_order_display}\n"
            f"Payment Status: PAID\n"
            f"Odoo Sales Order: {order.name}\n"
            f"Invoice: {invoice.name}\n"
            f"Invoice Status: Posted"
        )
        _logger.info("%s", log_posted)
        self.log_xml(
            message=log_posted,
            func='_auto_create_invoice_and_payment',
            level='Info',
        )

        self._register_ecommerce_invoice_payment(invoice, order, order_data)

    def _register_ecommerce_invoice_payment(self, invoice, order, order_data):
        """Register payment on a posted ecommerce invoice if not already paid."""
        if not invoice or invoice.state != 'posted':
            return
        if invoice.payment_state in ('paid', 'in_payment', 'reversed'):
            return
        try:
            journal = self._get_ecommerce_payment_journal()
            if not journal:
                return

            new_payment = {
                'payment_date': invoice.invoice_date or fields.Date.context_today(self),
                'journal_id': journal.id,
            }
            payment_provider_codes = order_data.get('payment_provider_codes') or []
            if payment_provider_codes:
                payment_method_line = self._find_or_create_payment_method(
                    journal=journal,
                    payment_provider_code=payment_provider_codes[0],
                )
                new_payment['payment_method_line_id'] = payment_method_line.id

            payment_register = self.env['account.payment.register'].with_context(
                active_model='account.move',
                active_ids=invoice.ids,
            ).create(new_payment)
            payment_register.action_create_payments()
        except Exception as pay_err:
            _logger.warning("Could not register payment for invoice %s on order %s: %s",
                           invoice.name, order.name, pay_err)

    def _process_utm_data(self, order, order_data):
        """Process utm data from ecommerce and set it on the sale order.

        :param order: `sale.order` record
        :param order_data: dict with order data from ecommerce platform
        """
        utm_data = order_data.get('utm_data')
        if not utm_data:
            return order
        utm_record_mapping = {}
        for utm_key in ['source', 'medium', 'campaign']:
            if utm_data.get(utm_key):
                utm_record = self.env[f'utm.{utm_key}'].search(
                    [('name', '=ilike', utm_data[utm_key])], limit=1,
                )
                if not utm_record:
                    utm_record = self.env[f'utm.{utm_key}'].create(
                        {'name': utm_data[utm_key]},
                    )
                utm_record_mapping[f'{utm_key}_id'] = utm_record.id
        if utm_record_mapping:
            order.write(utm_record_mapping)
        return order

    def _get_ecommerce_payment_journal(self):
        if self.payment_journal_id:
            return self.payment_journal_id
        # Fallback to first bank journal
        return self.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', self.company_id.id)],
            limit=1,
        )

    def _get_ecommerce_account_journal(self):
        if self.account_journal_id:
            return self.account_journal_id
        # Fallback to first sale journal
        return self.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', self.company_id.id)],
            limit=1,
        )

    def _process_inventory_data(self, inventory_data):
        """Process the provided inventory data and update the stock levels accordingly.

        :param dict inventory_data: The inventory data to process.
        :return: None
        """
        offer = self.ecommerce_offer_ids.filtered(lambda r: r.sku == inventory_data.get('sku') and r.sync_stock)

        if self.support_location:
            ecommerce_location = self.ecommerce_location_ids.filtered(lambda r: r.ecommerce_location_identifier == inventory_data.get('ecommerce_location_identifier') and r.sync_stock)
            location = ecommerce_location.matched_location_id
        else:
            location = self.location_id

        if not (offer and location) or 'quantity' not in inventory_data:
            return  # skip this inventory due to insufficient data.

        product = offer.matched_product_id
        Quant = self.env['stock.quant']
        # A product/location pair can be spread across several quants (an existing
        # reservation, a stale row left by a previous partial update, ...). Searching
        # with limit=1 and overwriting just that row's quantity leaves the others
        # untouched, so the *total* on-hand no longer matches what the E-commerce
        # platform reports. Summing every quant first and applying only the
        # difference keeps the total correct regardless of how many rows exist.
        # E-commerce platforms report a single flat on-hand number with no
        # lot/package/owner granularity, so quants split by those are intentionally
        # excluded from this baseline (`strict=True` with no lot/package/owner
        # gathers only the unassigned ones) rather than guessed at.
        current_qty = sum(Quant._gather(product, location, strict=True).mapped('quantity'))
        delta = inventory_data['quantity'] - current_qty
        if product.uom_id.compare(delta, 0):
            Quant._update_available_quantity(product, location, delta)

    def _post_process_after_picking_update_success(self, picking, identifier):
        """
        This method is called after a picking is successfully updated to the E-commerce.

        :param picking: Record of `stock.picking` that was updated.
        :param identifier: E-commerce picking identifier returned by the E-commerce
                        after a successful update.
        """
        picking.ecommerce_picking_identifier = str(identifier)
        picking.ecommerce_sync_status = 'done'
        _logger.info(
            "Picking %s updated successfully to %s.",
            picking.name, self.ecommerce_channel_id.name,
        )
        message_post = self.env._("This picking has been successfully updated to the %s.", self.ecommerce_channel_id.name)
        picking.message_post(body=message_post)

    def _post_process_after_picking_update_failed(self, picking, error):
        """
        This method is called when a picking fails to be updated to the E-commerce.

        :param picking: Record of `stock.picking` that failed to be updated.
        :param error: Error message describing the failure.
        """
        picking.ecommerce_sync_status = 'error'
        message_post = self.env._("Error while updating this picking to %s: %s", self.ecommerce_channel_id.name, error)
        picking.message_post(body=message_post)
        self._handle_sync_failure(
            flow="picking_update", error_messages=[{'ec_order_ref': picking.ecommerce_order_identifier, 'message': str(error).split('DETAIL')[0]}],
        )
        self.log_xml(
            "Error occurred while updating picking with id %s on %s account with id %s.\n"
            "Error description: %s" %
            (picking.id, self.ecommerce_channel_id.name, self.id, str(error).split('DETAIL')[0]),
            '_post_process_after_picking_update_failed',
            'server',
        )

    # === ABSTRACT METHODS === #

    def _fetch_products_from_ecommerce(self):
        """Override this method in each ecommerce module to
        fetch products from the ecommerce and return them.

        This method should return a dictionary with the following structure:
        {
            "products": [{
                "sku": "Stock Keeping Unit",
                "name": "Name of Product in E-commerce Platform",
                "ec_product_identifier": "Product/Offer ID in E-commerce Platform",
                "ec_product_template_identifier": "Product Template ID in E-commerce Platform",
                "variant_data": {
                    "list_price": "Selling price of product excluding tax",
                    "standard_price": "Cost price of product excluding tax",
                    "volume": "Volume of product",
                    "weight": "Weight of product",
                }
                ...other ecommerce-specific offer fields as needed
            },...],
        }

        :rtype: dict
        """
        return {}

    def _fetch_orders_from_ecommerce(self):
        """Override this method in ecommerce modules to
        fetch orders from the ecommerce and return them in following common format.

        :return: An order structure dictionary with the following keys:
        "orders": List of orders, Each item includes:
            - tax_included (bool): Indicates whether tax is included in the unit price during order creation.
            - total_amount(float): Total final amount of the order excluding refund.
            - id (str): Unique identifier for the order on the ecommerce platform.
            - reference (str): Unique order reference for merchant (eg: S00018, #00018)
            - date_order (str): Order confirmation date in '%Y-%m-%d %H:%M:%S' format.
            - invoice_date (str): Invoice date in '%Y-%m-%d %H:%M:%S' format.
            - status (str): Order status with following possible values:
                - 'confirmed': pending, processing, on-hold, confirmed, completed
                - 'canceled': canceled, failed, trash, fraud
            - currency_code (str): Order currency code (e.g.: 'INR', 'USD').
            - location_id (str): Identifier of the ecommerce location (ID, code, or reference) used to decrement stock quantity from the corresponding location in Odoo.

            - customer_id (str): ID, code or reference of the customer on ecommerce platform that uniquely identifies them.
            - billing_address (dict): Billing address fields:
                - name (str): Full name of the buyer or customer.
                - email (str): Email address of the buyer.
                - phone (str)
                - street (str)
                - street2 (str)
                - zip (str)
                - city (str)
                - state_code (str)
                - country_code (str)
                - is_company (bool): Whether the address is a company or not.
                - vat (str): If it is a company, VAT/Tax ID of it.
            - shipping_address (dict): Shipping address with same structure as above (required if fulfillment by merchant and not billing_address).
            - other_addresses (list of dict): Additional addresses (if any), same structure as above.

            - order_lines (list of dict): Order line items. Each item includes:
                - id (str): Internal line identifier.
                - description (str): Description of the product.
                - product_data (dict): Product details:
                    - name (str): Name of the product.
                    - sku (str): SKU of the product.
                    - ec_product_id (str): Unique identifier of the product in the ecommerce platform.
                    - variant_data (dict): Product details, used for product creation.
                        - list_price (float): Selling price of product excluding tax.
                        - standard_price (float): Cost price of product excluding tax.
                        - volume (float): Volume of product.
                        - weight (float): Weight of product.
                        - image_url: Image of product,
                        - barcode: Barcode of product,
                        - is_storable: Track Inventory status of product,
                    - (other ecommerce-specific ecommerce.offer attributes as needed)
                - qty_ordered (float): Quantity of the item ordered.
                - price_unit (float): Unit sale price without discount and according to the tax-included configuration.
                - price_subtotal (float): Total price without discount and according to the tax-included configuration.
                - discount_amount (float): Final discount amount on this order line.
                - tax_amount (float): Total tax amount applied to the item.

            - shipping_lines (list of dict): Shipping line items. Each item includes:
                - id (int): Internal line identifier.
                - description (str): Description of the shipping line.
                - shipping_code (str): Shipping method code.(used to find product for shipping line)
                - price_unit (float): Price of particular shipping line.
                - discount_amount (float): Discount amount excluding tax.
                - discount_tax (float): Tax reduction corresponding to the discount.
                - tax_amount (float): Total tax amount applied to this shipping line.

            - discount_lines (list of dict): Discount line items. Each item includes:
                - description (str): Description of the discount line.
                - price_unit (float): Discount price of particular discount line excluding tax.
                - tax_amount (float): Total tax amount applied to this discount line.

            - fulfillments (list of dict): Fulfillments, Each fulfillment includes:
                - ecommerce_picking_identifier (int): Internal fulfillment identifier.
                - date_done (str): Fulfillment completion date in '%Y-%m-%d %H:%M:%S' format.
                - status (str): Fulfillment status with following possible values:
                    'confirmed': pending, processing, on-hold, confirmed, completed
                    'canceled': canceled, failed, trash, fraud
                - line_items (list of dict): line items of fulfillment. Each item includes:
                    - ecommerce_line_identifier (int): Internal line identifier of sale order.
                    - ecommerce_move_identifier (int): Internal fulfillment move identifier.
                    - quantity (int): Quantity fulfilled on this fulfillment for particular line item.
                - carrier_id (str): Name of delivery carrier.
                - tracking_number (str): Tracking number of fulfillment.
                - location_id (str): Identifier of the ecommerce location (ID, code, or reference) used to decrement stock quantity from the corresponding location in Odoo.
                - shipping_address (dict): Shipping address with same structure as billing_address of order.

            - payment_provider_codes (list of str): Contains list of provider code for each sale transaction.

            - utm_data (dict): UTM parameters of the order with following keys:
                - campaign (str): Campaign name.
                - medium (str): Medium name.
                - source (str): Source name.

        :rtype: dict
        """
        return {}

    def _fetch_order_from_ecommerce_by_order_ref(self, ecommerce_order_ref):
        """Override this method in the ecommerce modules to
        fetch orders from the ecommerce by order reference and
        return them in the same format as `_fetch_orders_from_ecommerce`.

        :return: An order structure dictionary with the same keys as `_fetch_orders_from_ecommerce`.
        """
        return {}

    def _fetch_locations_from_ecommerce(self):
        """ Override this method in ecommerce module to
        fetch locations from the ecommerce and return them.

        This method should return a dictionary with the following structure:
        {
            "locations": [{
                "id": "Unique identifier of ecommerce location",
                "name": "Name of Location in E-commerce platform",
            },...],
        }
        :rtype: dict
        """
        return {}

    def _fetch_inventory_from_ecommerce(self):
        """Override this method in each ecommerce module to
        fetch inventory from the ecommerce and return them.

        This method should return a dictionary with the following structure:
        {
            "inventory": [{
                "ecommerce_location_identifier": str,  # Identifier of the location on the Ecommerce platform, In case of not support location exclude this key.
                "sku": str,  # SKU of the product
                "quantity": float,  # Total quantity (available + reserved) of the product on the e-commerce platform
            },...],
        }

        :rtype: dict
        """
        return {}

    def _update_pickings_to_ecommerce(self, pickings):
        """
        Override this method to update pickings

        :param pickings: stock.picking recordset to be updated to the E-commerce platform.

        Note:
            Each e-commerce platform must implement this logic appropriately.

            - If your E-commerce platform supports updating multiple pickings at once:
                * Update all pickings in a single request.
                * After processing, call `_post_process_after_picking_update_success` for successful pickings.
                * Call `_post_process_after_picking_update_failed` for failed pickings.

            - If your E-commerce platform does not support updating multiple pickings at once:
                * Update each picking individually.
                * On success, call `_post_process_after_picking_update_success`.
                * On failure, call `_post_process_after_picking_update_failed`.

            - If an error occurs for a single picking:
                * Do not raise the error at the base level.
                * Catch the error and call `_post_process_after_picking_update_failed` for that picking.
        """
        return {}

    def _update_inventory_to_ecommerce(self, inventory_data):
        """
        Override this method in each ecommerce model and implement the logic for updating inventory.

        Note: This method replace the quantity of particular product for particular location on Ecommerce platform.

        :param inventory_data: A list of dictionary containing inventory information to update.
            [
                {
                    "offer": record of ecommerce.offer,
                    "quantity": float,  # Quantity of the product to update
                    "location": record of ecommerce.location,  # location on which offer's inventory will be updated (not set if locations are not supported).
                },
                ...
            ]

        :rtype: dict
        """
        return {}

    def _handle_sync_failure(self, flow, data={}, error_messages=False, email_template_xmlid=None):
        """Send a mail to the responsible persons to report a synchronization failure.

        :param str flow: The flow for which the failure mail is requested. Supported flows are:
                        `inventory_update`, `order_sync`, `picking_update`.
        :param error_messages: A string for `order_sync` and `inventory_update`,
                              or a list of dictionaries for `picking_update`, where each item contains ec_order_ref and message.
        :return: None
        """
        self.ensure_one()
        _logger.error(
            "Failed to execute %s flow for %s Account %s with id %s: Error: %s",
            flow, self.ecommerce_channel_id.name, self.name, self.id, str(error_messages).split('DETAIL')[0],
        )
        flow_to_email_template_mapper = {
            'inventory_update': 'odoo_ecommerce.inventory_update_failure',
            'order_sync': 'odoo_ecommerce.order_sync_failure',
            'picking_update': 'odoo_ecommerce.picking_update_failure',
        }
        mail_template_id = email_template_xmlid or flow_to_email_template_mapper.get(flow)
        if not mail_template_id:
            _logger.error("Unknown flow %s for failure notification.", flow)
            return

        mail_template = self.env.ref(mail_template_id, raise_if_not_found=False)
        if not mail_template:
            _logger.warning("The mail template with xmlid %s has been deleted.", mail_template_id)
        else:
            responsible_emails = {user.email for user in filter(
                None, (self.user_id, self.env.ref('base.user_admin', raise_if_not_found=False))
            ) if user.email}
            if not responsible_emails:
                _logger.error("No responsible email found to handle %s failure.", flow)
                return
            mail_template.with_context(**{
                'email_to': ",".join(responsible_emails),
                'ec_account_id': self.id,
                'ec_channel_name': self.ecommerce_channel_id.name,
                'ec_account_name': self.name,
                'ec_channel_code': self.channel_code,
                'error_messages': error_messages,
                **data,
            }).send_mail(self.env.user.id)
            _logger.info("Sent synchronization failure notification email to %s", ', '.join(responsible_emails))
