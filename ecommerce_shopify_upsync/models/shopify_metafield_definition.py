import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.ecommerce_shopify import const
from odoo.addons.ecommerce_shopify_upsync import utils_upsync

_logger = logging.getLogger(__name__)


class ShopifyMetafieldDefinition(models.Model):
    """Centralized Shopify Metafield Definition configuration.

    Mirrors Shopify's product metafield definitions (ownerType PRODUCT).
    Each definition configured here is automatically made available across
    all Odoo product templates, while each product maintains its own separate
    value for the definition.
    """
    _name = 'shopify.metafield.definition'
    _description = 'Shopify Metafield Definition'
    _order = 'namespace, key'

    name = fields.Char(
        string="Name",
        required=True,
        help="Human-readable name of the metafield definition (e.g. 'Material', 'Care Instructions').",
    )
    namespace = fields.Char(
        string="Namespace",
        required=True,
        default='custom',
        help="Shopify namespace for the metafield (defaults to 'custom').",
    )
    key = fields.Char(
        string="Key",
        required=True,
        help="Unique key for the metafield within the namespace (e.g. 'material').",
    )
    type = fields.Selection(
        selection=const.SHOPIFY_METAFIELD_TYPES,
        string="Type",
        required=True,
        default='single_line_text_field',
        help="Shopify metafield type (e.g. 'single_line_text_field', 'number_integer', etc.).",
    )
    type_name = fields.Char(
        string="Type Key",
        compute='_compute_type_name',
        store=True,
        help="Shopify metafield type string identifier.",
    )
    description = fields.Text(
        string="Description",
        help="Description of what this metafield is used for.",
    )
    shopify_definition_id = fields.Char(
        string="Shopify Definition ID",
        copy=False,
        help="Shopify Metafield Definition GID or numeric ID.",
    )
    ecommerce_account_id = fields.Many2one(
        comodel_name='ecommerce.account',
        string="Shopify Account",
        ondelete='cascade',
        domain=[('channel_code', '=', 'shopify')],
        help="Shopify account this definition is associated with (optional).",
    )
    owner_type = fields.Char(
        string="Owner Type",
        required=True,
        default='PRODUCT',
        help="Shopify owner type, e.g. 'PRODUCT' or 'PRODUCTVARIANT'.",
    )
    product_metafield_ids = fields.One2many(
        comodel_name='shopify.product.metafield',
        inverse_name='definition_id',
        string="Product Values",
        help="Per-product metafield values associated with this definition.",
    )

    _unique_account_definition = models.Constraint(
        'UNIQUE(ecommerce_account_id, shopify_definition_id)',
        "A metafield definition can only be mirrored once per Shopify account.",
    )

    @api.depends('type')
    def _compute_type_name(self):
        for rec in self:
            rec.type_name = rec.type or 'single_line_text_field'

    @api.constrains('namespace', 'key', 'owner_type', 'ecommerce_account_id')
    def _check_unique_namespace_key(self):
        for record in self:
            owner = record.owner_type or 'PRODUCT'
            domain = [
                ('id', '!=', record.id),
                ('namespace', '=', record.namespace),
                ('key', '=', record.key),
                ('owner_type', '=', owner),
            ]
            if record.ecommerce_account_id:
                domain.append(('ecommerce_account_id', '=', record.ecommerce_account_id.id))
            else:
                domain.append(('ecommerce_account_id', '=', False))
            if self.search_count(domain):
                raise ValidationError(_(
                    "A metafield definition with namespace '%s', key '%s', and owner type '%s' already exists."
                ) % (record.namespace, record.key, owner))

    @api.constrains('shopify_definition_id', 'ecommerce_account_id')
    def _check_unique_shopify_definition_id(self):
        for record in self:
            if not record.shopify_definition_id:
                continue
            norm_id = utils_upsync.normalize_shopify_definition_id(record.shopify_definition_id)
            domain = [
                ('id', '!=', record.id),
                '|',
                ('shopify_definition_id', '=', record.shopify_definition_id),
                ('shopify_definition_id', '=', norm_id),
            ]
            if record.ecommerce_account_id:
                domain.append(('ecommerce_account_id', '=', record.ecommerce_account_id.id))
            if self.search_count(domain):
                raise ValidationError(_(
                    "A metafield definition with Shopify Definition ID '%s' already exists."
                ) % record.shopify_definition_id)

    def _sync_to_all_products(self):
        """Ensure every product.template has a shopify.product.metafield line for this definition."""
        templates = self.env['product.template'].search([])
        if not templates:
            return

        Metafield = self.env['shopify.product.metafield'].with_context(active_test=False)
        for defn in self:
            if not defn.namespace or not defn.key:
                continue
            existing_mfs = Metafield.search([
                ('namespace', '=', defn.namespace),
                ('key', '=', defn.key),
            ])
            # Link unlinked rows matching this namespace + key
            unlinked = existing_mfs.filtered(lambda m: not m.definition_id)
            if unlinked:
                unlinked.write({
                    'definition_id': defn.id,
                    'type': defn.type or defn.type_name or 'single_line_text_field',
                })

            existing_product_ids = set(existing_mfs.mapped('product_tmpl_id.id'))
            missing_templates = templates.filtered(lambda t: t.id not in existing_product_ids)

            to_create = []
            mf_type = defn.type or defn.type_name or 'single_line_text_field'
            for tmpl in missing_templates:
                to_create.append({
                    'product_tmpl_id': tmpl.id,
                    'definition_id': defn.id,
                    'namespace': defn.namespace,
                    'key': defn.key,
                    'type': mf_type,
                    'value': '',
                })
            if to_create:
                Metafield.create(to_create)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('type') and vals.get('type_name'):
                vals['type'] = vals['type_name']
            elif vals.get('type') and not vals.get('type_name'):
                vals['type_name'] = vals['type']
            vals.setdefault('owner_type', 'PRODUCT')
            if vals.get('shopify_definition_id'):
                vals['shopify_definition_id'] = utils_upsync.normalize_shopify_definition_id(
                    vals['shopify_definition_id']
                )
        records = super().create(vals_list)
        if not self.env.context.get('skip_product_metafield_sync'):
            records._sync_to_all_products()
        return records

    def write(self, vals):
        if not vals.get('type') and vals.get('type_name'):
            vals['type'] = vals['type_name']
        if 'shopify_definition_id' in vals and vals['shopify_definition_id']:
            vals['shopify_definition_id'] = utils_upsync.normalize_shopify_definition_id(
                vals['shopify_definition_id']
            )
        res = super().write(vals)
        if any(f in vals for f in ('namespace', 'key', 'type', 'owner_type')):
            for defn in self:
                lines = defn.product_metafield_ids
                line_vals = {}
                if 'namespace' in vals:
                    line_vals['namespace'] = defn.namespace
                if 'key' in vals:
                    line_vals['key'] = defn.key
                if 'type' in vals:
                    line_vals['type'] = defn.type
                if line_vals:
                    lines.write(line_vals)
                defn._sync_to_all_products()
        return res

    def unlink(self):
        # Delete un-synced blank placeholder lines for these definitions
        blank_lines = self.env['shopify.product.metafield'].search([
            ('definition_id', 'in', self.ids),
            ('value', 'in', [False, '']),
            ('shopify_metafield_id', '=', False),
        ])
        if blank_lines:
            blank_lines.unlink()
        return super().unlink()

    def action_pull_from_shopify(self):
        """Pull definitions and product values from connected Shopify account(s)."""
        accounts = self.mapped('ecommerce_account_id').filtered(
            lambda a: a.channel_code == 'shopify' and a.state == 'connected'
        )
        if not accounts:
            accounts = self.env['ecommerce.account'].search([
                ('channel_code', '=', 'shopify'),
                ('state', '=', 'connected'),
            ])
        if not accounts:
            raise UserError(_("No connected Shopify account found. Please connect an account first."))
        return accounts[:1].action_fetch_shopify_metafield_definitions()

    def action_mirror_to_shopify(self):
        """Mirror this metafield definition to Shopify.
        Checks if it already exists on Shopify before creating; if so, reuses it."""
        self.ensure_one()
        account = self.ecommerce_account_id
        if not account:
            account = self.env['ecommerce.account'].search([
                ('channel_code', '=', 'shopify'),
                ('state', '=', 'connected'),
            ], limit=1)
        if not account or account.state != 'connected':
            raise UserError(_("Please connect a Shopify account first."))

        res = utils_upsync.create_metafield_definition_on_shopify(
            account=account,
            namespace=self.namespace,
            key=self.key,
            mf_type=self.type or self.type_name or 'single_line_text_field',
            name=self.name,
            owner_type=self.owner_type or 'PRODUCT',
            shopify_definition_id=self.shopify_definition_id,
        )
        if res.get('definition_id'):
            self.write({
                'shopify_definition_id': res['definition_id'],
                'ecommerce_account_id': account.id,
            })
            _logger.info(
                "DEFINITION MAPPED: %s.%s mapped to Shopify Definition ID: %s",
                self.namespace, self.key, res['definition_id'],
            )
        if res.get('error'):
            raise UserError(_("Failed to mirror definition to Shopify: %s") % res['error'])

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Mirror Definition"),
                'message': _("Definition successfully mirrored/reused with Shopify."),
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }