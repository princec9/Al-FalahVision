/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

/**
 * "Add Component" button for sale.order.line rows.
 *
 * This replaces the native `type="object"` button so it also works on
 * a Sale Order that has not been saved yet.
 *
 * IMPORTANT: no server-side / business logic is changed here. This
 * widget simply:
 *   1. Saves the whole order first, ONLY if it (or this line) is not
 *      yet persisted - i.e. the same save that already happens when the
 *      user clicks the top "Save" (cloud) icon.
 *   2. Calls the existing `action_add_dynamic_component` method, exactly
 *      as the original button did.
 *   3. Reloads the form so the new component line appears immediately
 *      under its parent.
 *
 * For an order that is already saved and clean, step 1 is skipped
 * entirely, so behavior is identical to the original button.
 */
export class AddComponentButton extends Component {
    static template = "optical_prescription.AddComponentButton";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ loading: false });
    }

    // A component line can never itself have components, and a global
    // discount line (e.g. "Add a discount") is never a real product
    // line - no components either.
    // get isVisible() {
    //     return !this.props.record.data.parent_line_id
    //         && !this.props.record.data.is_reward_line;
    // }

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
        data.product_type !== "service" &&
        !data.parent_line_id &&
        !data.display_type &&
        !data.is_downpayment &&
        !isDiscountLine
    );
}
    async onClick() {
            console.log("Add button clicked");
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
                    "add_component_button: line has no resId yet, aborting this click."
                );
                return;
            }

            console.time("add");

await this.orm.call(
    "sale.order.line",
    "action_add_dynamic_component",
    [lineRecord.resId]
);

console.log("RPC finished");

await formRecord.load();

console.log("Reload finished");

console.timeEnd("add");
        } finally {
            this.state.loading = false;
        }
    }
}

registry.category("view_widgets").add("add_component_button", {
    component: AddComponentButton,
});

// /** @odoo-module **/
//
// import { registry } from "@web/core/registry";
// import { Component, useState } from "@odoo/owl";
// import { useService } from "@web/core/utils/hooks";
// import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
//
// // ---------------------------------------------------------------------
// // ROOT CAUSE OF THE "2 LINES ON THE 2ND CLICK" BUG
// // ---------------------------------------------------------------------
// // `this.state.loading` only guards ONE widget instance.
// //
// // On the 1st click no other row is in edit mode, so the click is
// // dispatched once and exactly one component line is created.
// //
// // From the 2nd click on, the previously added component row IS in edit
// // mode. Clicking the parent row's button therefore does two things at
// // once: it makes the editable list leave/commit the edited row, which
// // re-renders the row widgets (the button component is DESTROYED and a
// // brand new instance is mounted), and it fires the click. The click
// // ends up handled by both the old and the freshly mounted instance -
// // each with its own `state.loading = false` - so
// // `action_add_dynamic_component` is called TWICE and the server
// // legitimately creates two lines.
// //
// // The guard therefore has to live OUTSIDE the component instances,
// // keyed by the parent line. Nothing else changes: the server method,
// // the line creation logic, the hierarchy and the pricing are untouched.
// // ---------------------------------------------------------------------
// const ADD_COMPONENT_IN_FLIGHT = new Set();
// const ADD_COMPONENT_LAST_DONE = new Map();
// // Covers the re-dispatched click that arrives just after the first one
// // finished (re-render + click land in the same user gesture).
// const ADD_COMPONENT_COOLDOWN_MS = 250;
//
// /**
//  * "Add Component" button for sale.order.line rows.
//  *
//  * This replaces the native `type="object"` button so it also works on
//  * a Sale Order that has not been saved yet.
//  *
//  * IMPORTANT: no server-side / business logic is changed here. This
//  * widget simply:
//  *   1. Saves the whole order first, ONLY if it (or this line) is not
//  *      yet persisted - i.e. the same save that already happens when the
//  *      user clicks the top "Save" (cloud) icon.
//  *   2. Calls the existing `action_add_dynamic_component` method, exactly
//  *      as the original button did.
//  *   3. Reloads the form so the new component line appears immediately
//  *      under its parent.
//  *
//  * For an order that is already saved and clean, step 1 is skipped
//  * entirely, so behavior is identical to the original button.
//  */
// export class AddComponentButton extends Component {
//     static template = "optical_prescription.AddComponentButton";
//     static props = { ...standardWidgetProps };
//
//     setup() {
//         this.orm = useService("orm");
//         this.state = useState({ loading: false });
//     }
//
//     // A component line can never itself have components, and a global
//     // discount line (e.g. "Add a discount") is never a real product
//     // line - no components either.
//     // get isVisible() {
//     //     return !this.props.record.data.parent_line_id
//     //         && !this.props.record.data.is_reward_line;
//     // }
//
// get isVisible() {
//     const data = this.props.record.data;
//
//     const productName =
//         data.product_id?.display_name ??   // new-style object
//         data.product_id?.[1] ??             // old-style [id, name] array
//         "";
//
//     const isDiscountLine =
//         data.is_reward_line ||
//         productName === "Discount" ||
//         (data.name || "").trim().endsWith("%") && productName === "Discount";
//
//     return (
//         data.product_id &&
//         !data.parent_line_id &&
//         !data.display_type &&
//         !data.is_downpayment &&
//         !isDiscountLine
//     );
// }
//     /**
//      * Remove order_line rows that only exist client-side and were never
//      * filled in (the editable list's trailing placeholder row).
//      *
//      * A row is dropped ONLY when it is new (virtual id), has no product,
//      * no description, no display_type and no parent line - i.e. it holds
//      * nothing but ORM defaults. Everything else is left alone.
//      */
//     async _discardEmptyPendingLines(formRecord) {
//         const list = formRecord.data.order_line;
//         if (!list || !list.records) {
//             return;
//         }
//
//         const stray = list.records.filter((rec) => {
//             if (!rec.isNew) {
//                 return false;
//             }
//             const data = rec.data || {};
//             return (
//                 !data.product_id &&
//                 !data.display_type &&
//                 !data.parent_line_id &&
//                 !(data.name || "").trim()
//             );
//         });
//
//         for (const rec of stray) {
//             try {
//                 await list.delete(rec);
//                 console.warn(
//                     "add_component_button: dropped an unfilled pending order line."
//                 );
//             } catch (error) {
//                 // Never let this block the actual component creation.
//                 console.warn(
//                     "add_component_button: could not drop pending line.",
//                     error
//                 );
//             }
//         }
//     }
//
//     async onClick() {
//         console.log("Add button clicked");
//         if (this.state.loading) {
//             return;
//         }
//
//         const lineRecord = this.props.record;
//         const formRecord = lineRecord.model.root;
//
//         // Stable across re-renders: the Record object survives the row
//         // re-render, only the widget component is re-created.
//         const clickKey = lineRecord.resId || lineRecord.id;
//
//         // Same click already being processed by another (now destroyed)
//         // instance of this widget, or processed a few ms ago by it.
//         if (ADD_COMPONENT_IN_FLIGHT.has(clickKey)) {
//             console.warn(
//                 "add_component_button: duplicate click ignored (in flight)."
//             );
//             return;
//         }
//         const lastDone = ADD_COMPONENT_LAST_DONE.get(clickKey);
//         if (lastDone && Date.now() - lastDone < ADD_COMPONENT_COOLDOWN_MS) {
//             console.warn(
//                 "add_component_button: duplicate click ignored (cooldown)."
//             );
//             return;
//         }
//
//         ADD_COMPONENT_IN_FLIGHT.add(clickKey);
//         this.state.loading = true;
//         try {
//
//             // The whole order must be persisted BEFORE the server-side
//             // component line is created, because the `formRecord.load()`
//             // at the end of this handler drops anything still pending in
//             // the client.
//             //
//             // This used to be guarded by `formRecord.dirty`, which does
//             // not exist on the Odoo 19 Record (the getter is `isDirty`),
//             // so the guard was always falsy and the save was skipped.
//             // Consequence: on the 2nd click the product just picked on
//             // the previous component row was thrown away by the reload,
//             // and the user ended up looking at TWO blank component lines
//             // (the reverted one + the newly created one).
//             //
//             // Saving unconditionally is safe: `save()` is a no-op on a
//             // record that is neither new nor modified.
//             //
//             // BUT: the editable order_line list keeps a fresh VIRTUAL
//             // row open at the bottom after the previous component row
//             // was confirmed (Enter/Tab). That row is not the user's - it
//             // is the list's "next row" placeholder, carrying nothing but
//             // ORM defaults (qty 1.00, no product). Saving would turn it
//             // into a real sale.order.line, so one click produced two
//             // lines: this stray row + the one the server creates.
//             //
//             // Dropping it here is not "hiding a line" - it is not
//             // persisting a record the user never filled in. Any row with
//             // a product, a name, a section/note or an explicit parent is
//             // left completely untouched.
//             await this._discardEmptyPendingLines(formRecord);
//
//             const saved = await formRecord.save();
//             if (!saved) {
//                 // A validation error blocked the save; the framework
//                 // already surfaced the relevant message.
//                 return;
//             }
//
//             if (!lineRecord.resId) {
//                 // Silently bail out instead of showing a pop-up to the
//                 // user - this is a rare timing edge case, not a real
//                 // failure; clicking again works fine.
//                 console.warn(
//                     "add_component_button: line has no resId yet, aborting this click."
//                 );
//                 return;
//             }
//
//             console.time("add");
//
// await this.orm.call(
//     "sale.order.line",
//     "action_add_dynamic_component",
//     [lineRecord.resId]
// );
//
// console.log("RPC finished");
//
// await formRecord.load();
//
// console.log("Reload finished");
//
// console.timeEnd("add");
//         } finally {
//             ADD_COMPONENT_IN_FLIGHT.delete(clickKey);
//             ADD_COMPONENT_LAST_DONE.set(clickKey, Date.now());
//             this.state.loading = false;
//         }
//     }
// }
//
// registry.category("view_widgets").add("add_component_button", {
//     component: AddComponentButton,
// });