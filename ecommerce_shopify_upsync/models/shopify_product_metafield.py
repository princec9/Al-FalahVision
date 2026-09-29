# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.ecommerce_shopify import const


class ShopifyProductMetafield(models.Model):
    """Generic Shopify Metafields framework (namespace/key/type/value) attached to
    a product.template, pushed via `metafieldsSet` and pulled via the Admin
    GraphQL `product.metafields` connection — see
    `ecommerce.account._push_product_metafields_to_shopify` and
    `ecommerce.account._pull_product_metafields_to_odoo`.

    Also backs the Google Shopping fields: `product.template.action_add_google_shopping_metafields`
    pre-fills rows here under Shopify's own 'mm-google-shopping' namespace, instead
    of Odoo needing one dedicated field per Google Shopping attribute.
    """
    _name = 'shopify.product.metafield'
    _description = 'Shopify Product Metafield'
    _order = 'namespace, key'

    active = fields.Boolean(
        string="Active",
        default=True,
        help="Rows that were pulled from (or pushed to) Shopify but no longer exist "
             "there are archived — on delete on Shopify — instead of dropped, so the "
             "values stay traceable. Locally-prepared rows (no Shopify Metafield ID "
             "yet) are never auto-archived.",
    )
    product_tmpl_id = fields.Many2one(
        comodel_name='product.template',
        string="Product",
        required=True,
        ondelete='cascade',
        index=True,
    )
    definition_id = fields.Many2one(
        comodel_name='shopify.metafield.definition',
        string="Definition",
        ondelete='set null',
        index=True,
        help="Linked centralized Shopify Metafield Definition.",
    )
    name = fields.Char(
        string="Name",
        related='definition_id.name',
        store=True,
        readonly=True,
        help="Display name from the linked metafield definition.",
    )
    namespace = fields.Char(string="Namespace", required=True)
    key = fields.Char(string="Key", required=True)
    type = fields.Selection(
        selection=const.SHOPIFY_METAFIELD_TYPES,
        string="Type",
        required=True,
        default='single_line_text_field',
        help="Shopify metafield type, stored as Shopify's own type key (e.g. "
             "'single_line_text_field' -> 'Single Line Text'). Must match the "
             "metafield definition's type if one already exists on the Shopify store "
             "for this namespace/key. The stored value itself is what is sent to "
             "Shopify in metafieldsSet, so the type is always preserved, never "
             "hardcoded to text.",
    )
    value = fields.Text(
        string="Value",
        required=False,
        default='',
        help="Value for this metafield on this product.",
    )
    description = fields.Text(
        string="Description",
        related='definition_id.description',
        readonly=True,
    )
    shopify_metafield_id = fields.Char(
        string="Shopify Metafield ID",
        readonly=True,
        copy=False,
        help="Shopify Metafield ID (gid://shopify/Metafield/... — never the Product "
             "ID or a Metafield Definition ID) returned by Shopify after the "
             "metafield was pushed or pulled successfully.",
    )
    last_sync_date = fields.Datetime(
        string="Last Synced",
        readonly=True,
        copy=False,
        help="When this metafield was last pushed to / pulled from Shopify.",
    )
    last_sync_direction = fields.Selection(
        selection=[
            ('SHOPIFY_TO_ODOO', 'Shopify → Odoo'),
            ('ODOO_TO_SHOPIFY', 'Odoo → Shopify'),
        ],
        string="Last Sync Direction",
        readonly=True,
        copy=False,
    )
    last_sync_status = fields.Selection(
        selection=[
            ('success', 'Success'),
            ('failed', 'Failed'),
            ('error', 'Failed'),
        ],
        string="Last Sync Status",
        readonly=True,
        copy=False,
    )
    last_sync_error = fields.Text(
        string="Last Sync Error",
        readonly=True,
        copy=False,
    )

    _unique_product_namespace_key = models.Constraint(
        'UNIQUE(product_tmpl_id, namespace, key)',
        "Namespace + key must be unique per product.",
    )

    @api.onchange('definition_id')
    def _onchange_definition_id(self):
        if self.definition_id:
            self.namespace = self.definition_id.namespace
            self.key = self.definition_id.key
            self.type = self.definition_id.type or self.definition_id.type_name or 'single_line_text_field'

    @api.constrains('product_tmpl_id', 'namespace', 'key')
    def _check_unique_product_metafield(self):
        for record in self:
            domain = [
                ('id', '!=', record.id),
                ('product_tmpl_id', '=', record.product_tmpl_id.id),
                ('namespace', '=', record.namespace),
                ('key', '=', record.key),
            ]
            if self.with_context(active_test=False).search_count(domain):
                raise ValidationError(_(
                    "Product '%s' already has a metafield for namespace '%s' and key '%s'."
                ) % (record.product_tmpl_id.display_name, record.namespace, record.key))

    def unlink(self):
        # If the product template is being deleted, allow unlink.
        # Otherwise, archive the metafield so we know to delete it from Shopify on next sync/push.
        if self.env.context.get('force_unlink'):
            return super().unlink()
        
        to_archive = self.filtered(lambda m: m.shopify_metafield_id)
        if to_archive:
            to_archive.write({'active': False, 'value': ''})
            
        to_unlink = self - to_archive
        if to_unlink:
            return super(ShopifyProductMetafield, to_unlink).unlink()
        return True

    @api.model_create_multi
    def create(self, vals_list):
        Definition = self.env['shopify.metafield.definition']
        for vals in vals_list:
            if vals.get('definition_id'):
                defn = Definition.browse(vals['definition_id'])
                if defn.exists():
                    vals.setdefault('namespace', defn.namespace)
                    vals.setdefault('key', defn.key)
                    vals.setdefault('type', defn.type or defn.type_name or 'single_line_text_field')
            elif vals.get('namespace') and vals.get('key'):
                defn = Definition.search([
                    ('namespace', '=', vals['namespace']),
                    ('key', '=', vals['key']),
                ], limit=1)
                if defn:
                    vals['definition_id'] = defn.id
                    if not vals.get('type'):
                        vals['type'] = defn.type or defn.type_name or 'single_line_text_field'
            if 'value' not in vals or vals['value'] is False:
                vals['value'] = ''
        return super().create(vals_list)

    def write(self, vals):
        Definition = self.env['shopify.metafield.definition']
        if vals.get('definition_id') and (not vals.get('namespace') or not vals.get('key')):
            defn = Definition.browse(vals['definition_id'])
            if defn.exists():
                vals.setdefault('namespace', defn.namespace)
                vals.setdefault('key', defn.key)
                vals.setdefault('type', defn.type or defn.type_name or 'single_line_text_field')
        elif ('namespace' in vals or 'key' in vals) and 'definition_id' not in vals:
            for rec in self:
                ns = vals.get('namespace', rec.namespace)
                k = vals.get('key', rec.key)
                defn = Definition.search([
                    ('namespace', '=', ns),
                    ('key', '=', k),
                ], limit=1)
                if defn:
                    vals['definition_id'] = defn.id
        return super().write(vals)