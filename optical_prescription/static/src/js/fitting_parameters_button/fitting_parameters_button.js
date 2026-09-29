/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

/**
 * "Fitting Parameters" button for sale.order.line rows.
 *
 * This replaces the native `type="object"` button so it also works on
 * a Sale Order that has not been saved yet.
 *
 * IMPORTANT: no server-side / business logic is changed here. This
 * widget simply:
 *   1. Saves the whole order first, ONLY if it (or this line) is not
 *      yet persisted - i.e. the same save that already happens when the
 *      user clicks the top "Save" (cloud) icon.
 *   2. Calls the existing `action_open_fitting_parameters` method, exactly
 *      as the original button did.
 *   3. Opens the returned action (the Fitting Parameters form dialog),
 *      exactly as the original button did.
 *
 * For an order that is already saved and clean, step 1 is skipped
 * entirely, so behavior is identical to the original button.
 */
export class FittingParametersButton extends Component {
    static template = "optical_prescription.FittingParametersButton";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: false });
    }

    // Same visibility rule as the "Add Component" button: only a real,
    // parent product line shows this button - not component lines, not
    // display/section lines, not down-payment lines and not discount
    // lines.
    get isVisible() {
        const data = this.props.record.data;

        const productName =
            data.product_id?.display_name ??   // new-style object
            data.product_id?.[1] ??             // old-style [id, name] array
            "";

        const isDiscountLine =
            data.is_reward_line ||
            productName === "Discount" ||
            (data.name || "").trim().endsWith("%") && productName === "Discount";

        return (
            data.product_id &&
                data.is_lab_order &&
                    data.product_type !== "service" &&   // ADD THIS
            !data.parent_line_id &&
            !data.display_type &&
            !data.is_downpayment &&
            !isDiscountLine
        );
    }

    async onClick() {
        if (this.state.loading) {
            return;
        }
        this.state.loading = true;
        try {
            const lineRecord = this.props.record;
            const formRecord = lineRecord.model.root;

            // Only save if needed: brand-new order, or unsaved edits
            // (e.g. the product on this row was just picked).
            if (formRecord.isNew || formRecord.dirty) {
                const saved = await formRecord.save({ stayInEdition: true });
                if (!saved) {
                    // A validation error blocked the save; the framework
                    // already surfaced the relevant message.
                    return;
                }
            }

            if (!lineRecord.resId) {
                // Silently bail out instead of showing a pop-up to the
                // user - this is a rare timing edge case, not a real
                // failure; clicking again works fine.
                console.warn(
                    "fitting_parameters_button: line has no resId yet, aborting this click."
                );
                return;
            }

            const result = await this.orm.call(
                "sale.order.line",
                "action_open_fitting_parameters",
                [lineRecord.resId]
            );
            console.log(result);

            if (result) {
                await this.action.doAction(result);
            }
        } finally {
            this.state.loading = false;
        }
    }
}

registry.category("view_widgets").add("fitting_parameters_button", {
    component: FittingParametersButton,
});
