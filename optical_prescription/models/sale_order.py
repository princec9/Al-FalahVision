# -*- coding: utf-8 -*-
import datetime
from email.policy import default
from html import escape

from markupsafe import Markup
import logging

_logger = logging.getLogger(__name__)
from odoo import api, fields, models, _
import datetime
from dateutil.relativedelta import relativedelta
from odoo.exceptions import ValidationError, UserError
from odoo.tools import float_compare

from odoo import models, fields


class SaleCommissionPlan(models.Model):
    _inherit = 'sale.commission.plan'
#
#     incentive_enabled = fields.Boolean(
#         string="Enable Incentive"
#     )
#
#     incentive_role = fields.Selection([
#         ('staff', 'Staff'),
#         ('incharge', 'In-Charge'),
#     ], compute='_compute_incentive_role', store=True)
#
#     personal_target = fields.Monetary(
#         currency_field='currency_id'
#     )
#
#     fixed_incentive = fields.Monetary(
#         currency_field='currency_id'
#     )
#
#     additional_incentive_rate = fields.Float(
#         string="Additional Incentive %",
#         help="Rate applied on the amount achieved above the target, on top "
#              "of the fixed incentive."
#     )
#
#     shop_target = fields.Monetary("Shop Target", currency_field='currency_id')
#
    commission_based_on = fields.Selection([
        ('sale_order', 'Sale Order'),
        ('sale_order_line', 'Sale Order Line'),
    ], string='Commission Based On', default='sale_order', required=True,
       tracking=True)
#
#     @api.depends('commission_based_on')
#     def _compute_incentive_role(self):
#         for plan in self:
#             if plan.commission_based_on == 'sale_order_line':
#                 plan.incentive_role = 'staff'
#             else:
#                 plan.incentive_role = 'incharge'

class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = "sale.advance.payment.inv"

    def create_invoices(self):
        """
        Prevent invoice creation when a Sale Order contains Lab Order
        products whose Manufacturing Orders are not confirmed.

        Normal Sale Orders without Lab products follow standard Odoo flow.
        """

        for wizard in self:
            sale_orders = wizard.sale_order_ids

            for order in sale_orders:

                # Find Lab master/parent lines only
                lab_lines = order.order_line.filtered(
                    lambda line: (
                        line.is_lab_order
                        and not line.parent_line_id
                        and not line.display_type
                        and line.product_id
                    )
                )

                # No Lab product -> normal Odoo invoice flow
                if not lab_lines:
                    continue

                # Check every Lab Manufacturing Order
                for line in lab_lines:

                    # Lab product exists but MO was not created
                    if not line.production_id:
                        raise UserError(_(
                            "You cannot create the invoice yet.\n\n"
                            "Lab Product: %s\n"
                            "Manufacturing Order has not been created.\n\n"
                            "Please create the Lab Manufacturing Order first."
                        ) % line.product_id.display_name)

                    production = line.production_id

                    # MO must be confirmed or beyond
                    allowed_states = (
                        "confirmed",
                        "progress",
                        "to_close",
                        "done",
                    )

                    # if production.state not in allowed_states and wizard.advance_payment_method == 'delivered':
                    #     raise UserError(_(
                    #         "You cannot create the invoice yet.\n\n"
                    #         "Lab Product: %s\n"
                    #         "Manufacturing Order: %s\n"
                    #         "Manufacturing Order Status: %s\n\n"
                    #         "Please confirm the Manufacturing Order before "
                    #         "creating the invoice."
                    #     ) % (
                    #         line.product_id.display_name,
                    #         production.name,
                    #         production.state,
                    #     ))

        # Everything is valid -> continue with standard Odoo invoice creation
        return super().create_invoices()
class SaleOrder(models.Model):
    _inherit = "sale.order"
    user_id = fields.Many2one(
        'res.users',
        string='Salesperson',
        domain="[('company_ids', '=', company_id)]",
    )



    @api.depends('partner_id')
    def _compute_user_id(self):
        for order in self:
            order.user_id = False

    def _get_invoiceable_lines(self, final=False):

        # Standard Odoo invoiceable lines.

        # This already includes normal products without Lab Order.

        invoiceable_lines = super()._get_invoiceable_lines(

            final=final

        )

        for order in self:
            # Lab master products must not appear in the invoice.

            lab_parent_lines = order.order_line.filtered(

                lambda line: (

                        line.is_lab_order

                        and not line.parent_line_id

                        and not line.display_type

                )

            )

            # Components belonging to Lab Order parent products.

            lab_component_lines = order.order_line.filtered(

                lambda line: (

                        line.parent_line_id

                        and line.parent_line_id.is_lab_order

                        and not line.display_type

                        and line.product_id

                        and line.qty_to_invoice > 0

                )

            )

            # Remove only Lab parent products.

            invoiceable_lines -= lab_parent_lines

            # Add Lab components.

            invoiceable_lines |= lab_component_lines
            _logger.info("==== Invoiceable Lines ====")
            for line in invoiceable_lines:
                _logger.info(
                    "Invoiceable: id=%s product=%s display=%s downpayment=%s qty=%s",
                    line.id,
                    line.product_id.display_name,
                    line.display_type,
                    line.is_downpayment,
                    line.qty_to_invoice,
                )

        return invoiceable_lines

    def _get_order_lines_to_report(self):
        """Filter lines shown on quotation and sale order reports.
        A parent line that has components is just a zero-priced
        container - only its components carry real product/price data,
        so the parent itself is skipped on the printed report."""
        self.ensure_one()
        lines = super()._get_order_lines_to_report()
        return lines.filtered(
            lambda line: not (
                    not line.parent_line_id
                    and line.child_component_ids
            )
        )

    def action_confirm(self):

        # Additive guard: only fires for lines that are actually flagged as
        # awaiting discount approval. Orders without excessive discounts
        # confirm exactly as they do today.
        for order in self:
            pending = order.order_line.filtered("discount_approval_required")
            if pending:
                raise UserError(_(
                    "The following lines carry a discount above the "
                    "salesperson's limit and are still awaiting approval:\n\n"
                    "%s\n\n"
                    "Use the \"Request Approval\" button on each line before "
                    "confirming this order."
                ) % "\n".join(
                    " - %s (%.2f%% requested, %.2f%% allowed)" % (
                        line.product_id.display_name or line.name,
                        line.discount or 0.0,
                        line.salesperson_discount_limit,
                    )
                    for line in pending
                ))

        for order in self:

            lab_lines = order.order_line.filtered(

                lambda line: (

                        line.is_lab_order

                        and not line.parent_line_id

                        and not line.display_type

                        and line.product_id

                        and line.product_uom_qty > 0

                        and not line.production_id

                )

            )

            # Validate lab order components before Sale Order confirmation

            for line in lab_lines:

                component_lines = order.order_line.filtered(

                    lambda component: (

                            component.parent_line_id == line

                            and not component.display_type

                    )

                )

                if not component_lines:
                    raise UserError(

                        _(

                            "Please add at least one component for %s."

                        ) % line.product_id.display_name

                    )

                invalid_components = component_lines.filtered(

                    lambda component: (

                            not component.product_id

                            or component.product_uom_qty <= 0

                    )

                )

                if invalid_components:
                    raise UserError(

                        _(

                            "Please select all component products and enter "

                            "a quantity greater than zero for %s."

                        ) % line.product_id.display_name

                    )

            # Create Lab Manufacturing Orders automatically

            if lab_lines:
                order._create_lab_manufacturing_orders(

                    sale_order_lines=lab_lines

                )

        # Standard Sale Order confirmation

        result = super().action_confirm()

        # Link delivery with Lab Manufacturing Orders

        for order in self:
            order._link_lab_delivery_to_production()

        return result

    def _link_lab_delivery_to_production(self):
        self.ensure_one()

        lab_lines = self.order_line.filtered(
            lambda line: (
                    line.is_lab_order
                    and not line.parent_line_id
                    and not line.display_type
                    and line.product_id
                    and line.production_id
            )
        )
        if not lab_lines:
            return True

        for line in lab_lines:
            finished_moves = line.production_id.move_finished_ids.filtered(
                lambda m: m.state != "cancel"
            )
            if not finished_moves:
                continue

            own_delivery_moves = line.move_ids.filtered(
                lambda m: m.state not in ("done", "cancel")
            )
            if not own_delivery_moves:
                continue

            own_delivery_moves.write({
                "procure_method": "make_to_order",
                "move_orig_ids": [
                    fields.Command.link(m.id) for m in finished_moves
                ],
            })
            # Safety net in case reservation already ran on confirm
            # before this hook set procure_method.
            own_delivery_moves._do_unreserve()

        return True

    quality_check_count = fields.Integer(

        string="Quality Checks",

        compute="_compute_quality_check_counts",

    )

    quality_pass_count = fields.Integer(

        string="Passed",

        compute="_compute_quality_check_counts",

    )
    technician_id = fields.Many2one('hr.employee',

                                    string="Technician",

                                    )
    technician_contact_id = fields.Many2one('res.partner',

                                    string="Technician",

                                    )

    quality_fail_count = fields.Integer(

        string="Failed",

        compute="_compute_quality_check_counts",

    )

    production_ids = fields.Many2many(

        comodel_name="mrp.production",

        compute="_compute_production_ids",

        string="Lab Order Details",  # Changed from Manufacturing Orders

    )

    production_count = fields.Integer(

        string="Manufacturing Orders",

        compute="_compute_production_ids",

    )

    prescribed_ids = fields.Many2many("res.partner")

    prescription_id = fields.Many2one('prescription.records')

    prescription_ids = fields.One2many(

        comodel_name="prescription.records",

        inverse_name="order_id",

        string="Prescriptions",

    )

    prescription_count = fields.Integer(

        string="Prescription Count",

        compute="_compute_prescription_count",

    )

    def action_assign_to_lab(self):

        self.ensure_one()

        lab_lines = self.order_line.filtered(

            lambda line: (

                    line.is_lab_order

                    and not line.parent_line_id

                    and not line.display_type

                    and line.product_id

                    and line.product_uom_qty > 0

                    and not line.production_id

            )

        )

        if not lab_lines:
            raise UserError(

                _("There are no lab order lines ready to create.")

            )

        for line in lab_lines:

            component_lines = self.order_line.filtered(

                lambda component: (

                        component.parent_line_id == line

                        and not component.display_type

                )

            )

            if not component_lines:
                raise UserError(

                    _("Please add at least one component for %s.")

                    % line.product_id.display_name

                )

            invalid_components = component_lines.filtered(

                lambda component: (

                        not component.product_id

                        or component.product_uom_qty <= 0

                )

            )

            if invalid_components:
                raise UserError(

                    _(

                        "Please select all component products and enter "

                        "a quantity greater than zero for %s."

                    ) % line.product_id.display_name

                )

        productions = self._create_lab_manufacturing_orders(

            sale_order_lines=lab_lines

        )

        if len(productions) == 1:
            return {

                "type": "ir.actions.act_window",

                "name": _("Lab Manufacturing Order"),

                "res_model": "mrp.production",

                "view_mode": "form",

                "res_id": productions.id,

                "target": "current",

            }

        return {

            "type": "ir.actions.act_window",

            "name": _("Lab Manufacturing Orders"),

            "res_model": "mrp.production",

            "view_mode": "list,form",

            "domain": [("id", "in", productions.ids)],

            "target": "current",

        }

    def _create_lab_manufacturing_orders(self, sale_order_lines=None):

        self.ensure_one()

        sale_order_lines = (

                sale_order_lines

                or self.order_line.filtered(

            lambda line: (

                    line.is_lab_order

                    and not line.parent_line_id

                    and not line.display_type

                    and line.product_id

                    and line.product_uom_qty > 0

                    and not line.production_id

            )

        )

        )

        productions = self.env["mrp.production"]

        for main_line in sale_order_lines:

            component_lines = self.order_line.filtered(

                lambda component: (

                        component.parent_line_id == main_line

                        and component.product_id

                        and not component.display_type

                        and component.product_uom_qty > 0

                )

            )

            production_vals = {

                "product_id": main_line.product_id.id,

                "product_qty": main_line.product_uom_qty,

                "product_uom_id": main_line.product_uom_id.id,

                "origin": self.name,

                "company_id": self.company_id.id,
                "technician_id": self.technician_id.id,
                "technician_contact_id": self.technician_contact_id.id,

            }

            production = self.env["mrp.production"].create(

                production_vals

            )

            raw_move_commands = []

            for component_line in component_lines:
                component_product = component_line.product_id

                raw_move_commands.append(

                    (0, 0, {

                        "product_id": component_product.id,

                        "product_uom_qty": (

                            component_line.product_uom_qty

                        ),

                        "product_uom": (

                                component_line.product_uom_id.id

                                or component_product.uom_id.id

                        ),

                        "location_id": (

                            production.location_src_id.id

                        ),

                        "location_dest_id": (

                            component_product

                            .with_company(self.company_id)

                            .property_stock_production.id

                        ),

                        "company_id": self.company_id.id,

                    })

                )

            if raw_move_commands:
                production.write({

                    "move_raw_ids": raw_move_commands,

                })

            main_line.production_id = production.id

            productions |= production

        return productions

    def _compute_quality_check_counts(self):

        for order in self:
            quality_lines = order.order_line.filtered(

                lambda line: (

                        line.is_lab_order

                        and not line.display_type

                        and line.quality_result

                )

            )

            order.quality_check_count = len(quality_lines)

            order.quality_pass_count = len(quality_lines.filtered(lambda line: line.quality_result == "pass"))

            order.quality_fail_count = len(quality_lines.filtered(lambda line: line.quality_result == "fail"))

    def _action_open_quality_lines(self, result=None):

        self.ensure_one()

        domain = [

            ("order_id", "=", self.id),

            ("is_lab_order", "=", True),

            ("quality_result", "!=", False),

        ]

        if result:
            domain.append(("quality_result", "=", result))

        return {

            "name": _("Quality Check Details"),

            "type": "ir.actions.act_window",

            "res_model": "sale.order.line",

            "view_mode": "list,form",

            "domain": domain,

            "context": {

                "create": False,

                "edit": False,

            },

        }

    def action_view_quality_checks(self):

        return self._action_open_quality_lines()

    def action_view_passed_quality_checks(self):

        return self._action_open_quality_lines(result="pass")

    def action_view_failed_quality_checks(self):

        return self._action_open_quality_lines(result="fail")

    @api.depends("order_line.production_id")
    def _compute_production_ids(self):

        for order in self:
            productions = order.order_line.mapped("production_id")

            order.production_ids = productions

            order.production_count = len(productions)

    def action_view_manufacturing_orders(self):

        self.ensure_one()

        productions = self.production_ids

        action = self.env["ir.actions.actions"]._for_xml_id("mrp.mrp_production_action")

        if len(productions) == 1:

            action.update({

                "view_mode": "form",

                "res_id": productions.id,

                "views": [(False, "form")],

            })

        else:

            action["domain"] = [("id", "in", productions.ids)]

        return action

    @api.depends("prescription_ids")
    def _compute_prescription_count(self):

        for order in self:
            order.prescription_count = len(order.prescription_ids)

    def add_prescription_record_from_line(self):

        for rec in self:

            if not rec.prescription_id:
                val = [x.prescription_id.id for x in rec.order_line if x and x.prescription_id]

                rec.prescription_id = val[0] if val else False

    def action_add_prescription(self):

        self.ensure_one()

        return {

            "name": _("Add Prescription For '%s'") % (self.name or "/"),

            "type": "ir.actions.act_window",

            "res_model": "prescription.records",

            "view_mode": "form",

            "views": [(False, "form")],

            "target": "new",

            "context": {

                "default_order_id": self.id,

                "default_partner_id": self.partner_id.id,

                "default_res_model": "sale.order",

                "default_line_res_id": self.id,

            },

        }

    def action_open_prescription(self):

        self.ensure_one()

        return {

            "name": _("Prescription Records"),

            "type": "ir.actions.act_window",

            "res_model": "prescription.records",

            "view_mode": "list,form",

            "domain": [("order_id", "=", self.id)],

            "context": {

                "default_order_id": self.id,

                "default_partner_id": self.partner_id.id,

            },

        }


class SaleOrderLineComponent(models.Model):
    _name = 'sale.order.line.component'

    _description = 'Sale Order Line Component'

    sale_order_line_id = fields.Many2one(

        'sale.order.line',

        string='Parent Sale Line',

        ondelete='cascade',

        required=True

    )

    product_id = fields.Many2one('product.product', string="Component", required=True)

    product_uom_id = fields.Many2one('uom.uom', string="Unit of Measure")

    quantity = fields.Float(string="Quantity", default=1.0)

    unit_price = fields.Monetary(string="Unit Price")

    subtotal = fields.Monetary(string="Subtotal", compute="_compute_subtotal")

    currency_id = fields.Many2one(related="sale_order_line_id.currency_id")

    component_type = fields.Selection([

        ('frame', 'Frame'),

        ('lens', 'Lens'),

        ('other', 'Other')

    ], string="Component Type", default="other")

    note = fields.Text(string="Note")

    @api.depends('quantity', 'unit_price')
    def _compute_subtotal(self):

        for comp in self:
            comp.subtotal = comp.quantity * comp.unit_price

    @api.onchange('product_id')
    def _onchange_product_id(self):

        if self.product_id:
            self.unit_price = self.product_id.lst_price

            self.product_uom_id = self.product_id.uom_id.id


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"
    product_type = fields.Selection(
        related="product_id.type",
        string="Product Type",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # DISCOUNT % <-> DISCOUNT AMOUNT MIRROR
    #
    # `discount_amount` is a CONVENIENCE / DISPLAY field only.
    #
    #     discount (%)     --compute-->   discount_amount
    #     discount_amount  --inverse-->   discount (%)
    #
    # The single source of truth for every price, tax, subtotal, invoice,
    # commission and approval computation stays Odoo's standard
    # `discount` (%) field. `discount_amount` is NEVER subtracted from the
    # line a second time, so the two can never stack.
    # ------------------------------------------------------------------

    def _get_discount_base(self):
        """Gross line value the discount applies to (Unit Price x Qty)."""
        self.ensure_one()
        return (self.price_unit or 0.0) * (self.product_uom_qty or 0.0)

    def _discount_pct_from_amount(self):
        """Percentage equivalent of the currently entered amount."""
        self.ensure_one()
        base = self._get_discount_base()
        if not base:
            return None
        pct = (self.discount_amount or 0.0) * 100.0 / base
        return min(max(pct, 0.0), 100.0)

    @api.depends("price_unit", "product_uom_qty", "discount")
    def _compute_discount_amount(self):
        """Derive the amount from the percentage actually being applied."""
        for line in self:
            base = line._get_discount_base()
            line.discount_amount = base * (line.discount or 0.0) / 100.0

    def _inverse_discount_amount(self):
        """User typed an amount -> translate it into the standard %."""
        for line in self:
            pct = line._discount_pct_from_amount()
            if pct is None:
                continue
            if line.discount != pct:
                line.discount = pct

    @api.onchange("discount_amount")
    def _onchange_discount_amount(self):
        """Live form feedback.

        `inverse` only runs on save, so the percentage is mirrored here as
        well to keep the on-screen values consistent while editing.
        """
        for line in self:
            pct = line._discount_pct_from_amount()
            if pct is None:
                continue
            line.discount = pct

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ['discount_amount']

    @api.model
    def _load_pos_data_domain(self, data, config_id=False):
        domain = super()._load_pos_data_domain(data, config_id)

        # Same rule as in PosSession._loader_params_sale_order_line:
        # hide any parent/master line that has components, only allow
        # normal products and component lines to load into the POS.
        domain += [
            ('child_component_ids', '=', False),
            '|', '|',
            ('is_lab_order', '=', False),
            ('is_store_order', '=', False),
            ('parent_line_id', '!=', False),
        ]

        return domain

    def _get_sale_order_fields(self):
        return super()._get_sale_order_fields() + ['discount_amount']

    fitting_parameter_id = fields.Many2one("fitting.parameters")
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Salesperson",
    )

    def action_open_fitting_parameters(self):
        self.ensure_one()

        fitting = self.env["fitting.parameters"].search(
            [("sale_line_id", "=", self.id)],
            limit=1,
        )

        if fitting:
            return {
                "type": "ir.actions.act_window",
                "name": "Fitting Parameters",
                "res_model": "fitting.parameters",

                "views": [(False, "form")],
                "res_id": fitting.id,
                "target": "new",  # popup
            }

        return {
            "type": "ir.actions.act_window",
            "name": "Fitting Parameters",
            "res_model": "fitting.parameters",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_sale_line_id": self.id,
            },
        }

    @api.depends(
        "product_uom_qty",
        "qty_invoiced",
        "qty_delivered",
        "state",
        "parent_line_id",
        "parent_line_id.is_lab_order",
        "is_lab_order",
    )
    def _compute_qty_to_invoice(self):
        super()._compute_qty_to_invoice()

        for line in self:
            # Lab parent (container) line: never invoiced, nothing to invoice
            if (
                    line.is_lab_order
                    and not line.parent_line_id
                    and not line.display_type
            ):
                line.qty_to_invoice = 0.0
                continue

            if (
                    line.parent_line_id
                    and line.parent_line_id.is_lab_order
                    and line.order_id.state in ("sale", "done")
            ):
                line.qty_to_invoice = max(
                    line.product_uom_qty - line.qty_invoiced,
                    0.0,
                )


    @api.depends(
        "state",
        "product_uom_qty",
        "qty_delivered",
        "qty_to_invoice",
        "qty_invoiced",
        "is_lab_order",
        "parent_line_id",
    )
    def _compute_invoice_status(self):
        super()._compute_invoice_status()
        for line in self:
            if (
                    line.state == "sale"
                    and line.is_lab_order
                    and not line.parent_line_id
                    and not line.display_type
            ):
                line.invoice_status = "invoiced"



    def _prepare_invoice_line(self, **optional_values):
        self.ensure_one()
        _logger.info(
            "Preparing invoice lineeeeeeeeee: %s (%s)",
            self.id,
            self.product_id.display_name,
        )
        values = super()._prepare_invoice_line(**optional_values)

        # `discount` (%) is carried over untouched by super() -- the invoice
        # therefore follows the standard Odoo discount flow. The invoice
        # line's own `discount_amount` is a mirror field and is recomputed
        # there from price_unit / quantity / discount, so it is deliberately
        # NOT pushed through here.
        values.update({
            "user_id": self.user_id.id,
        })
        return values

    def _action_launch_stock_rule(self, previous_product_uom_qty=False):

        """

        Prevent child component lines from creating delivery stock moves.



        Delivery:

            Normal product       -> YES

            Lab master product   -> YES

            Child component      -> NO

        """

        delivery_lines = self.filtered(

            lambda line: not line.parent_line_id

        )

        if not delivery_lines:
            return True

        return super(

            SaleOrderLine,

            delivery_lines,

        )._action_launch_stock_rule(

            previous_product_uom_qty=previous_product_uom_qty

        )

    parent_line_id = fields.Many2one(

        'sale.order.line',

        string='Parent Master Line',

        ondelete='cascade',

        index=True,

    )

    child_component_ids = fields.One2many(

        comodel_name="sale.order.line",

        inverse_name="parent_line_id",

        string="Components",

    )

    dynamic_component_ids = fields.One2many(

        "sale.order.line.component",

        "sale_order_line_id",

        string="Dynamic Components",

        copy=True,

    )

    dynamic_component_count = fields.Integer(

        string="Component Count",

        compute="_compute_dynamic_component_values",

    )

    dynamic_component_total = fields.Monetary(

        string="Component Total",

        compute="_compute_dynamic_component_values",

        currency_field="currency_id",

    )

    discount_amount = fields.Monetary(
        string="Disc. Amount",
        currency_field="currency_id",
        compute="_compute_discount_amount",
        inverse="_inverse_discount_amount",
        store=True,
        readonly=False,
        copy=False,
        help="Discount expressed as an amount. This is a mirror of the "
             "Discount (%) field - enter either one and the other is "
             "derived automatically. Only Discount (%) is used in the "
             "price, tax, invoice and commission calculations.",
    )

    salesperson_id = fields.Many2one(

        comodel_name="hr.employee",

        string="Salesperson",

        domain=[("share", "=", False)],

    )

    failure_reason = fields.Text(

        string="Failure Reason",

        copy=False,

    )

    is_lab_order = fields.Boolean(
        related="product_id.is_lab_order",
        store=False,
    )

    is_store_order = fields.Boolean(

        string="Store Order",

        default=False,

    )

    key_point_check = fields.Text(

        string="Key Point Check",

    )

    quality_manager_id = fields.Many2one(

        comodel_name="res.users",

        string="Quality Check Manager",

        domain=[("share", "=", False)],

    )

    quality_result = fields.Selection(

        selection=[

            ("pass", "Pass"),

            ("fail", "Fail"),

        ],

        string="Quality Result",

    )

    quality_description = fields.Text(

        string="Additional Description",

    )

    production_id = fields.Many2one(

        comodel_name="mrp.production",

        string="Manufacturing Order",

        readonly=True,

        copy=False,

        ondelete="set null",

    )

    def _update_master_component_price(self):

        """

        The Lab Order parent product must always remain zero.

        Component lines retain their own amounts.

        """

        parents = self.mapped("parent_line_id")

        parents |= self.filtered(

            lambda line: (

                    line.is_lab_order

                    and not line.parent_line_id

            )

        )

        for parent in parents:
            if not parent.parent_line_id and parent.child_component_ids:
                if parent.price_unit != 0.0:
                    parent.with_context(
                        skip_component_price_update=True
                    ).write({
                        "price_unit": 0.0,
                        "discount": 0.0,
                    })
                    # parent.with_context(
                    #     skip_component_price_update=True
                    # ).write({
                    #     "price_unit": 0.0,
                    # })

    @api.onchange(
        "is_lab_order",
        "is_store_order",
    )
    def _onchange_parent_component_price(self):
        for line in self:
            if not line.parent_line_id and line.child_component_ids:
                line.price_unit = 0.0
                line.discount = 0.0

    @api.onchange(

        "product_id",

        "product_uom_qty",

        "price_unit",

        "parent_line_id",

    )
    def _onchange_component_line_price(self):

        """

        Keep the lab parent/master product price at zero.

        """

        for line in self:

            if line.parent_line_id:
                line.parent_line_id.price_unit = 0.0
            if (
                    not line.parent_line_id
                    and line.child_component_ids
            ):
                line.price_unit = 0.0

            # if line.is_lab_order and not line.parent_line_id:
            #     line.price_unit = 0.0

    @api.model_create_multi
    def create(self, vals_list):

        lines = super().create(vals_list)

        # Main lab product always starts at zero.

        main_lines = lines.filtered(
            lambda line: (
                not line.parent_line_id
            )
        )
        for line in main_lines:
            if line.child_component_ids:
                line.with_context(
                    skip_component_price_update=True
                ).write({
                    "price_unit": 0.0,
                    "discount": 0.0,
                })

        component_lines = lines.filtered("parent_line_id")

        if component_lines:
            component_lines._update_master_component_price()

        return lines

    # ---------------------------------------------------------

    # WRITE

    # ---------------------------------------------------------

    def write(self, vals):

        # Approved lines are frozen. Internal recalculation writes (which the
        # module already tags with skip_component_price_update) and explicit
        # bypasses are exempt.
        if not (
                self.env.context.get("skip_component_price_update")
                or self.env.context.get("bypass_discount_lock")
        ):
            self._check_discount_lock(vals)

        # An approved line that is still a quotation stays editable. Every
        # change to its discount is journalled so the audit trail survives
        # even though the line was never frozen.
        self._log_quotation_discount_edit(vals)

        # A discount approval is granted against one specific salesperson.
        # If the line is reassigned, the previous approval no longer applies
        # and the line falls back to the new salesperson's own limit.
        if "user_id" in vals and not self.env.context.get(
                "skip_discount_approval_reset"
        ):
            reassigned = self.filtered(
                lambda line: line.user_id.id != vals.get("user_id")
                             and line.discount_approved_percentage
            )
            if reassigned:
                reassigned.with_context(
                    skip_discount_approval_reset=True
                )._reset_discount_approval()

        if self.env.context.get("skip_component_price_update"):
            return super().write(vals)

        old_parents = self.mapped("parent_line_id")

        # Prevent manual editing of the main lab product price.

        main_lab_lines = self.filtered(

            lambda line: (

                    line.is_lab_order

                    and not line.parent_line_id

            )

        )

        if "price_unit" in vals and main_lab_lines:
            vals = dict(vals)
            if "price_unit" in vals:
                vals.pop("price_unit")

            if "discount_amount" in vals:
                vals.pop("discount_amount")

            if "discount" in vals:
                vals.pop("discount")

            # vals.pop("price_unit", None)

        result = super().write(vals)

        fields_affecting_component_price = {

            "product_id",

            "product_uom_qty",

            "price_unit",

            "discount",

            "discount_amount",

            "tax_ids",

            "parent_line_id",

        }

        if fields_affecting_component_price.intersection(vals):
            affected_lines = self | old_parents

            affected_lines._update_master_component_price()

        # When main product quantity changes, recalculate its unit price.

        if "product_uom_qty" in vals:
            main_lab_lines._update_master_component_price()

        return result

    # ---------------------------------------------------------

    # UNLINK

    # ---------------------------------------------------------

    def unlink(self):

        parents = self.mapped("parent_line_id")

        result = super().unlink()

        if parents:
            parents._update_master_component_price()

        return result

    def action_add_dynamic_component(self):
        self.ensure_one()

        if self.parent_line_id:
            raise UserError(_("A component cannot contain another component."))

        # Clicking Add Component marks this parent as a Store Order and
        # automatically turns it into a Lab Order too, since it now has
        # components that need to go through manufacturing. This is what
        # action_confirm() / action_assign_to_lab() key off of to create
        # the Manufacturing Order - no manual checkbox needed.
        vals_to_write = {}
        if not self.is_store_order:
            vals_to_write["is_store_order"] = True
        if not self.is_lab_order:
            vals_to_write["is_lab_order"] = True
        if vals_to_write:
            self.write(vals_to_write)

        component_lines = self.order_id.order_line.filtered(
            lambda line: line.parent_line_id == self
        )

        # IDEMPOTENCY GUARD
        # -----------------
        # An empty placeholder ("Select Component", no product yet) is
        # the row the user is meant to fill in right now. A second empty
        # placeholder is never useful, and it is exactly what a
        # double-dispatched click produces. If one already exists, this
        # call is a no-op and simply returns the order.
        #
        # This does NOT change the workflow: once the placeholder has a
        # product, the next click creates a new line as before.
        if component_lines.filtered(
                lambda line: not line.product_id and not line.display_type
        ):
            return {
                "type": "ir.actions.act_window",
                "res_model": "sale.order",
                "res_id": self.order_id.id,
                "view_mode": "form",
                "target": "current",
            }

        next_sequence = (
            max(component_lines.mapped("sequence")) + 1
            if component_lines
            else (self.sequence or 10) + 1
        )
        self.env["sale.order.line"].create({
            "order_id": self.order_id.id,
            "parent_line_id": self.id,
            "product_id": False,
            "name": _("Select Component"),
            "product_uom_qty": 1.0,
            "price_unit": 0.0,
            "sequence": next_sequence,
        })

        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": self.order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.depends(

        "dynamic_component_ids",

        "dynamic_component_ids.quantity",

        "dynamic_component_ids.unit_price",

    )
    def _compute_dynamic_component_values(self):

        for line in self:
            line.dynamic_component_count = len(line.dynamic_component_ids)

            line.dynamic_component_total = sum(line.dynamic_component_ids.mapped("subtotal"))

    def action_open_dynamic_components(self):

        self.ensure_one()

        if self.display_type:
            return False

        return {

            "name": _("Components - %s", self.product_id.display_name or self.name),

            "type": "ir.actions.act_window",

            "res_model": "sale.order.line.component",

            "view_mode": "list,form",

            "domain": [("sale_order_line_id", "=", self.id)],

            "context": {

                "default_sale_order_line_id": self.id,

                "default_company_id": self.company_id.id,

            },

            "target": "current",

        }

    def get_dynamic_component_values(self):

        """Return component data for optional MRP/lab integrations."""

        self.ensure_one()

        return [

            {

                "component_type": component.component_type,

                "product_id": component.product_id.id,

                "product_uom_id": component.product_uom_id.id,

                "quantity": component.quantity,

                "unit_price": component.unit_price,

                "note": component.note,

            }

            for component in self.dynamic_component_ids

        ]

    def _create_lab_manufacturing_orders(self, sale_order_line=None):

        for order in self:

            lines = sale_order_line or order.order_line

            lab_lines = lines.filtered(

                lambda line: (

                        line.is_lab_order

                        and line.quality_result == "pass"

                        and not line.display_type

                        and line.product_id

                        and line.product_uom_qty > 0

                        and not line.production_id

                )

            )

            for line in lab_lines:

                # 1. Create the main Manufacturing Order using the line's workcenter

                production = self.env["mrp.production"].create({

                    "product_id": line.product_id.id,

                    "product_qty": line.product_uom_qty,

                    "product_uom_id": line.product_uom_id.id,

                    "origin": order.name,

                    "company_id": order.company_id.id,

                    # "workcenter_id": line.workcenter_id.id if hasattr(self.env["mrp.production"],

                    #                                                   "workcenter_id") else False,

                })

                # 2. Dynamically add your custom components as raw material moves

                raw_moves = []

                for component in line.dynamic_component_ids:

                    if not component.product_id:
                        continue

                    raw_moves.append((0, 0, {

                        "product_id": component.product_id.id,

                        "product_uom_qty": component.quantity * line.product_uom_qty,

                        "product_uom": component.product_uom_id.id or component.product_id.uom_id.id,

                        "name": component.product_id.display_name,

                        "location_id": production.location_src_id.id,

                        "location_dest_id": component.product_id.with_company(

                            order.company_id).property_stock_production.id,

                        "company_id": order.company_id.id,

                    }))

                if raw_moves:
                    production.write({"move_raw_ids": raw_moves})

                line.production_id = production.id

    @api.depends(

        "product_uom_qty",

        "price_unit",

        "discount",

        "tax_ids",

        "is_lab_order",

        "parent_line_id",

    )
    def _compute_amount(self):

        super()._compute_amount()

        for line in self:

            # Only the main Lab Order parent line must be zero.

            if line.is_lab_order and not line.parent_line_id:
                line.price_subtotal = 0.0

                line.price_tax = 0.0

                line.price_total = 0.0

    # NOTE: `_prepare_base_line_for_taxes_computation` is intentionally NOT
    # overridden. Subtotal / tax / total now follow the standard Odoo
    # pipeline driven by `discount` (%) alone.

    @api.constrains(
        "discount_amount",
        "discount",
        "price_unit",
        "product_uom_qty",
    )
    def _check_discount_amount(self):
        for line in self:
            if line.display_type:
                continue
            if line.discount_amount < 0:
                raise ValidationError(
                    _("Discount amount cannot be negative.")
                )
            # Skip the lab master line because its price is calculated
            # automatically from component values.
            if line.is_lab_order and not line.parent_line_id:
                continue

            base = line._get_discount_base()

            if base > 0 and line.discount_amount > base:
                raise ValidationError(
                    _(
                        "The discount amount cannot exceed the line total "
                        "(Unit Price x Quantity)."
                    )
                )

    # ------------------------------------------------------------------
    # DISCOUNT APPROVAL LAYER
    #
    # Standalone validation layer. It never changes how Odoo computes
    # discounts, prices, taxes or commissions -- it only records whether an
    # above-limit discount has been blessed by an authorised employee.
    # ------------------------------------------------------------------

    salesperson_discount_limit = fields.Float(
        string="Salesperson Discount Limit (%)",
        compute="_compute_salesperson_discount_limit",
        help="Maximum discount the salesperson on this line may grant "
             "without approval.",
    )

    discount_approval_required = fields.Boolean(
        string="Discount Approval Required",
        compute="_compute_discount_approval_required",
        store=True,
        readonly=True,
        copy=False,
    )

    discount_approved_by_id = fields.Many2one(
        "hr.employee",
        string="Discount Approved By",
        readonly=True,
        copy=False,
    )

    discount_approved_date = fields.Datetime(
        string="Discount Approval Date",
        readonly=True,
        copy=False,
    )

    discount_approved_percentage = fields.Float(
        string="Approved Discount (%)",
        readonly=True,
        copy=False,
        help="Highest discount approved so far for this line. Approval is "
             "only requested again if the discount goes above this value.",
    )

    discount_approved_additional = fields.Float(
        string="Approved Additional (%)",
        readonly=True,
        copy=False,
        help="Portion of the approved discount that exceeded the "
             "salesperson's own limit.",
    )

    discount_locked = fields.Boolean(
        string="Discount Locked",
        compute="_compute_discount_locked",
        store=True,
        readonly=True,
        copy=False,
        help="Set once the discount on this line has been approved. "
             "A locked line can no longer be edited commercially.",
    )

    # Fields frozen once the line's discount has been approved. Anything not
    # listed here (quantities delivered/invoiced, analytic distribution,
    # state, ...) keeps behaving exactly as standard Odoo does.
    _DISCOUNT_LOCKED_FIELDS = (
        "product_id",
        "name",
        "product_uom_qty",
        "price_unit",
        "discount",
        "discount_amount",
        "tax_ids",
        "user_id",
    )

    @api.depends("discount_approved_by_id", "order_id.state")
    def _compute_discount_locked(self):
        """A line locks only once BOTH are true: its discount was approved,
        AND the Sale Order has been confirmed.

        While the order is still a quotation the approval is recorded but the
        line stays fully editable, so the salesperson can keep negotiating.
        Every edit made in that window is written to the order's chatter by
        `_log_quotation_discount_edit`. Confirming the order is what freezes
        the line.
        """
        for line in self:
            line.discount_locked = bool(
                line.discount_approved_by_id
                and line.order_id.state == "sale"
            )

    def _check_discount_lock(self, vals):
        """Refuse commercial edits on lines whose discount was approved."""
        locked = self.filtered("discount_locked")
        if not locked:
            return

        touched = [f for f in self._DISCOUNT_LOCKED_FIELDS if f in vals]
        if not touched:
            return

        blocked = []
        for line in locked:
            for fname in touched:
                if line._is_locked_field_changed(fname, vals[fname]):
                    blocked.append((line, fname))
                    break

        if not blocked:
            # Every value written is identical to what is already stored:
            # a no-op write must not be turned into an error.
            return

        raise UserError(_(
            "The following sale order lines are locked because their "
            "discount has been approved:\n\n%s\n\n"
            "A Sales Manager can release a line with the \"Unlock\" button "
            "before it can be edited again."
        ) % "\n".join(
            " - %s (approved by %s)" % (
                line.product_id.display_name or line.name,
                line.discount_approved_by_id.sudo().name,
            )
            for line, _fname in blocked
        ))

    def _is_locked_field_changed(self, fname, new_value):
        """Best-effort no-op detection so identical rewrites pass through."""
        self.ensure_one()
        field = self._fields[fname]
        current = self[fname]

        if field.type == "many2one":
            return current.id != new_value
        if field.type in ("many2many", "one2many"):
            # Command lists are not worth diffing; treat as a real change.
            return bool(new_value)
        if field.type == "float":
            return float_compare(
                current or 0.0, new_value or 0.0, precision_digits=4
            ) != 0
        return current != new_value

    def _log_quotation_discount_edit(self, vals):
        """Journal discount changes made on an ALREADY APPROVED line while the
        order is still a quotation.

        This is the audit trail that replaces the lock during the quotation
        window. It is deliberately silent for:
          * lines that were never approved (nothing to audit yet),
          * confirmed orders (those lines are locked, so no edit gets here),
          * no-op writes (same value rewritten by a recompute).

        Posted BEFORE super().write() on purpose: if the write then fails, the
        message is rolled back with it, so the chatter can never claim a change
        that did not happen.
        """
        if self.env.context.get("skip_discount_edit_log"):
            return
        if "discount" not in vals and "discount_amount" not in vals:
            return

        for line in self:
            if not line.discount_approved_by_id:
                continue
            if line.order_id.state == "sale":
                continue

            old_pct = line.discount or 0.0
            if "discount" in vals:
                new_pct = vals.get("discount") or 0.0
            else:
                # Only the mirror field was written; derive the resulting %.
                base = line._get_discount_base()
                new_pct = (
                    (vals.get("discount_amount") or 0.0) * 100.0 / base
                    if base else 0.0
                )

            if float_compare(old_pct, new_pct, precision_digits=2) == 0:
                continue

            order = line.order_id
            if not order:
                continue

            order.message_post(body=Markup(
                "<p><b>%(title)s</b></p>"
                "<ul>"
                "<li>%(l_product)s: %(product)s</li>"
                "<li>%(l_from)s: %(old)s</li>"
                "<li>%(l_to)s: %(new)s</li>"
                "<li>%(l_appr)s: %(appr)s</li>"
                "<li>%(l_by)s: %(by)s</li>"
                "</ul>"
                "<p><i>%(note)s</i></p>"
            ) % {
                                        "title": _("Discount Edited on Quotation"),
                                        "l_product": _("Product"),
                                        "product": (line.product_id.display_name
                                                    or line.name or _("Line")),
                                        "l_from": _("Discount Before"),
                                        "old": "%.2f%%" % old_pct,
                                        "l_to": _("Discount After"),
                                        "new": "%.2f%%" % new_pct,
                                        "l_appr": _("Already Approved Up To"),
                                        "appr": "%.2f%%" % (line.discount_approved_percentage or 0.0),
                                        "l_by": _("Edited By"),
                                        "by": self.env.user.name,
                                        "note": _(
                                            "The order is still a quotation, so the line is not "
                                            "locked. A new approval is only requested if the discount "
                                            "goes above the approved level."
                                        ),
                                    })

    def action_unlock_discount_approval(self):
        """Release an approved line so it can be edited again.

        Reserved to Sales Managers: unlocking discards the approval, so the
        line will ask for a fresh one if the discount is still above limit.
        """
        if not self.env.user.has_group("sales_team.group_sale_manager"):
            raise UserError(_(
                "Only a Sales Manager can unlock an approved discount line."
            ))

        for line in self:
            if not line.discount_locked:
                continue

            approver = line.discount_approved_by_id.sudo().name
            approved_pct = line.discount_approved_percentage

            line.with_context(
                skip_discount_approval_reset=True
            )._reset_discount_approval()

            order = line.order_id
            if order:
                order.message_post(body=Markup(
                    "<p><b>%(title)s</b></p>"
                    "<ul>"
                    "<li>%(l_product)s: %(product)s</li>"
                    "<li>%(l_prev)s: %(prev)s</li>"
                    "<li>%(l_by)s: %(by)s</li>"
                    "<li>%(l_user)s: %(user)s</li>"
                    "</ul>"
                ) % {
                                            "title": _("Discount Approval Released"),
                                            "l_product": _("Product"),
                                            "product": (line.product_id.display_name
                                                        or line.name or _("Line")),
                                            "l_prev": _("Previously Approved"),
                                            "prev": "%.2f%%" % (approved_pct or 0.0),
                                            "l_by": _("Originally Approved By"),
                                            "by": approver or _("Undefined"),
                                            "l_user": _("Unlocked By"),
                                            "user": self.env.user.name,
                                        })

        return True

    @api.depends("user_id")
    def _compute_salesperson_discount_limit(self):
        for line in self:
            line.salesperson_discount_limit = line._get_salesperson_limit()

    def _get_salesperson_limit(self):
        """Discount limit of the employee behind this line's salesperson.

        Read with sudo(): a salesperson is not necessarily allowed to read
        other employees' records, and the limit is needed for every line.
        """
        self.ensure_one()
        user = self.user_id
        if not user:
            return 0.0
        employee = user.sudo().employee_id
        if not employee:
            employee = user.sudo().employee_ids[:1]
        return employee.sale_discount_limit if employee else 0.0

    @api.depends(
        "discount",
        "user_id",
        "salesperson_discount_limit",
        "discount_approved_percentage",
        "display_type",
    )
    def _compute_discount_approval_required(self):
        for line in self:
            line.discount_approval_required = line._needs_discount_approval()

    def _needs_discount_approval(self):
        """True when the discount is above the salesperson's limit and has
        not already been approved at that level."""
        self.ensure_one()
        if self.display_type:
            return False
        discount = self.discount or 0.0
        # Within the salesperson's own limit -> never needs approval.
        if float_compare(
                discount, self.salesperson_discount_limit, precision_digits=2
        ) <= 0:
            return False
        # Already approved at this level or higher -> no new approval.
        if float_compare(
                discount, self.discount_approved_percentage or 0.0,
                precision_digits=2
        ) <= 0:
            return False
        return True

    def _reset_discount_approval(self):
        """Drop a previous approval: it was granted for a specific
        salesperson and is no longer meaningful once that changes."""
        self.write({
            "discount_approved_by_id": False,
            "discount_approved_date": False,
            "discount_approved_percentage": 0.0,
            "discount_approved_additional": 0.0,
        })

    def action_open_discount_approval_wizard(self):
        self.ensure_one()

        limit = self.salesperson_discount_limit
        additional = max((self.discount or 0.0) - limit, 0.0)

        return {
            "type": "ir.actions.act_window",
            "name": _("Discount Approval"),
            "res_model": "sale.discount.approval.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_line_id": self.id,
                "default_current_discount": self.discount or 0.0,
                "default_salesperson_limit": limit,
                "default_additional_required": additional,
            },
        }

    @api.onchange("discount", "user_id")
    def _onchange_discount_approval(self):
        """Warn the user as soon as the typed discount exceeds the
        salesperson's limit. Purely informational -- the value is left
        untouched so nothing in the standard flow changes."""
        self.ensure_one()
        has_discount = (
                (self.discount or 0.0) > 0.0
                or (self.discount_amount or 0.0) > 0.0
        )

        if has_discount and not self.user_id:
            self.discount = 0.0
            self.discount_amount = 0.0
            return {
                "warning": {
                    "title": _("Salesperson Required"),
                    "message": _(
                        "Please select a Salesperson before adding a discount."
                    ),
                }
            }

        if not self._needs_discount_approval():
            return

        limit = self.salesperson_discount_limit
        additional = (self.discount or 0.0) - limit

        return {
            "warning": {
                "title": _("Approval Required"),
                "message": _(
                    "Discount exceeds the salesperson's limit of %.2f%%. "
                    "Please request approval."
                ) % limit,
            }
        }
