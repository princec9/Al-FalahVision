from collections import defaultdict
from odoo import fields, models, api


class MailActivity(models.Model):
    """Inherited mail.activity model mostly to add dashboard functionalities"""
    _inherit = "mail.activity"

    activity_tag_ids = fields.Many2many(
        'activity.tag',
        string='Activity Tags',
        help='Select activity tags.',
    )
    state = fields.Selection([
        ('planned', 'Planned'),
        ('today', 'Today'),
        ('done', 'Done'),
        ('overdue', 'Overdue'),
    ], string='State', help='State of the activity',
        compute='_compute_state', store=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # If activity is not linked to a document → assign user
            if not vals.get('res_model') or not vals.get('res_id'):
                vals['user_id'] = vals.get('user_id') or self.env.user.id

        return super().create(vals_list)

    def _action_done(self, feedback=False, attachment_ids=None):
        """Override _action_done to keep done activities visible on dashboard.

        Odoo 19 removed _classify_by_model() and keep_done flag from
        mail.activity.type.  We replicate the core logic while preserving
        dashboard state by setting state='done' and active=False instead of
        unlinking the record.
        """
        messages = self.env['mail.message']
        next_activities_values = []

        # Collect attachments linked to these activities before any write/unlink
        attachments = self.env['ir.attachment'].search_read(
            [('res_model', '=', self._name), ('res_id', 'in', self.ids)],
            ['id', 'res_id'],
        )
        activity_attachments = defaultdict(list)
        for att in attachments:
            activity_attachments[att['res_id']].append(att['id'])

        for activity in self:
            # Prepare chained next activities
            if activity.chaining_type == 'trigger':
                vals = (
                    activity
                    .with_context(activity_previous_deadline=activity.date_deadline)
                    ._prepare_next_activity_values()
                )
                next_activities_values.append(vals)

            # Only post a message if this activity is linked to a document.
            # Standalone dashboard activities have no res_model/res_id so we
            # skip the message_post step to avoid a KeyError on env[False].
            if activity.res_model and activity.res_id:
                record = self.env[activity.res_model].browse(activity.res_id)

                # Post the "activity done" message on the origin record
                activity_message = record.message_post_with_source(
                    'mail.message_activity_done',
                    attachment_ids=attachment_ids,
                    render_values={
                        'activity': activity,
                        'feedback': feedback,
                        'display_assignee': activity.user_id != self.env.user,
                    },
                    mail_activity_type_id=activity.activity_type_id.id,
                    subtype_xmlid='mail.mt_activities',
                )

                # Re-attach any activity attachments to the posted message
                msg_att_ids = list(attachment_ids or []) + activity_attachments.get(activity.id, [])
                if msg_att_ids:
                    att_records = self.env['ir.attachment'].browse(msg_att_ids)
                    att_records.write({
                        'res_id': activity_message.id,
                        'res_model': activity_message._name,
                    })
                    activity_message.attachment_ids = att_records

                messages += activity_message

        # Create next chained activities
        next_activities = self.env['mail.activity']
        if next_activities_values:
            next_activities = self.env['mail.activity'].create(next_activities_values)

        # Mark as done and archive instead of unlinking so dashboard keeps records
        for rec in self:
            if rec.state != 'done':
                rec.state = 'done'
                rec.active = False

        return messages, next_activities

    def get_activity(self, activity_id):
        """Return the origin model and res_id for a given activity."""
        activity = self.env['mail.activity'].browse(activity_id)
        return {
            'model': activity.res_model,
            'res_id': activity.res_id,
        }
