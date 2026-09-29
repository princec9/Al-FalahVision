# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

_logger = logging.getLogger(__name__)

def migrate(cr, version):
    if not version:
        return

    stale_fields = (
        "disable_many2one_quick_create",
    )
    total_updated = 0

    for field_name in stale_fields:
        cr.execute(
            """
            UPDATE ir_ui_view
               SET arch_db = (regexp_replace(arch_db::text, %s, '', 'g'))::jsonb
             WHERE model = 'res.config.settings'
               AND arch_db::text LIKE %s::text
            """,
            (
                rf'<field[^>]*name="{field_name}"[^>]*/>',
                f"%{field_name}%",
            ),
        )
        if cr.rowcount:
            total_updated += cr.rowcount
            _logger.info(
                "odoo_ecommerce post-migrate: removed stale %s field nodes from %s "
                "settings view(s).",
                field_name,
                cr.rowcount,
            )

    if total_updated:
        cr.execute(
            """
            UPDATE ir_ui_view
               SET arch_updated = FALSE
             WHERE model = 'res.config.settings'
               AND active = TRUE
            """
        )
