

from markupsafe import Markup
import logging

_logger = logging.getLogger(__name__)
from odoo import  fields, models, _

from odoo.exceptions import ValidationError


class QualityCheckWizard(models.TransientModel):
    _inherit = "quality.check.wizard"

    assigned_employee_id = fields.Many2one(

        "hr.employee",

        string="Conducted Person",

        required=True,

    )

    failure_reason = fields.Text(

        string="Failure Reason",

    )

    failure_action = fields.Text(

        string="Failure Action",

    )

    def _get_quality_document(self, check):

        """

        Return the related document:

        - Manufacturing Order

        - Delivery / Picking

        """

        if check.production_id:
            return check.production_id

        if check.picking_id:
            return check.picking_id

        return False

    def _get_quality_document_type(self, check):

        if check.production_id:
            return _("Manufacturing Quality Check")

        if check.picking_id:
            return _("Delivery Quality Check")

        return _("Quality Check")

    def do_fail(self):

        for wizard in self:

            check = wizard.current_check_id

            if not wizard.assigned_employee_id:
                raise ValidationError(

                    _("Conducted Person is mandatory.")

                )

            if not wizard.failure_reason:
                raise ValidationError(

                    _(

                        "Failure Reason is mandatory before "

                        "failing the Quality Check."

                    )

                )

            if not wizard.failure_action:
                raise ValidationError(

                    _(

                        "Failure Action is mandatory before "

                        "failing the Quality Check."

                    )

                )

            check.write({

                "assigned_employee_id": wizard.assigned_employee_id.id,

                "failure_reason": wizard.failure_reason,

                "failure_action": wizard.failure_action,

            })

            document = wizard._get_quality_document(check)

            if document:
                document.message_post(

                    body=Markup("""

                        <div>

                            <strong>

                                ❌ %(quality_type)s Failed

                            </strong>



                            <br/><br/>



                            <strong>Quality Check:</strong>

                            %(check)s

                            <br/>



                            <strong>Product:</strong>

                            %(product)s

                            <br/>



                            <strong>Conducted Person:</strong>

                            %(employee)s

                            <br/>



                            <strong>Checked By:</strong>

                            %(user)s

                            <br/>



                            <strong>Failure Reason:</strong>

                            %(reason)s

                            <br/>



                            <strong>Failure Action:</strong>

                            %(action)s

                        </div>

                    """) % {

                        "quality_type": (

                            wizard._get_quality_document_type(check)

                        ),

                        "check": check.display_name,

                        "product": (

                            check.product_id.display_name

                            if check.product_id

                            else "-"

                        ),

                        "employee": (

                            wizard.assigned_employee_id.name

                        ),

                        "user": self.env.user.name,

                        "reason": wizard.failure_reason,

                        "action": wizard.failure_action,

                    },

                    subtype_xmlid="mail.mt_note",

                )

        return super().do_fail()

    def do_pass(self):

        for wizard in self:

            check = wizard.current_check_id

            if not wizard.assigned_employee_id:
                raise ValidationError(

                    _("Conducted Person is mandatory.")

                )

            check.write({

                "assigned_employee_id": wizard.assigned_employee_id.id,

                "failure_reason": False,

                "failure_action": False,

            })

            document = wizard._get_quality_document(check)

            if document:
                document.message_post(

                    body=Markup("""

                        <div>

                            <strong>

                                ✅ %(quality_type)s Passed

                            </strong>



                            <br/><br/>



                            <strong>Quality Check:</strong>

                            %(check)s

                            <br/>



                            <strong>Product:</strong>

                            %(product)s

                            <br/>



                            <strong>Conducted Person:</strong>

                            %(employee)s

                            <br/>



                            <strong>Checked By:</strong>

                            %(user)s

                        </div>

                    """) % {

                        "quality_type": (

                            wizard._get_quality_document_type(check)

                        ),

                        "check": check.display_name,

                        "product": (

                            check.product_id.display_name

                            if check.product_id

                            else "-"

                        ),

                        "employee": (

                            wizard.assigned_employee_id.name

                        ),

                        "user": self.env.user.name,

                    },

                    subtype_xmlid="mail.mt_note",

                )

        return super().do_pass()


class QualityCheck(models.Model):
    _inherit = "quality.check"

    failure_reason = fields.Text(

        string="Failure Reason",

    )

    failure_action = fields.Text(

        string="Failure Action",

    )

    assigned_employee_id = fields.Many2one(

        "hr.employee",

        string="Conducted Person",

    )
