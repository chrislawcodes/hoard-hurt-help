// Timezone auto-fill for the admin date-window filter forms (/admin/reports,
// /admin/engagement — see app/templates/admin/_date_window_form.html). Both
// forms share this hidden field via name="tz", so one selector works on both
// pages without needing to know either page's element id.
//
// Fills the field with the browser's IANA timezone name, but ONLY when the
// field is still empty. A page reload after submitting a filter echoes the
// timezone the server already parsed back into this field's value attribute;
// overwriting that unconditionally would silently swap the window the report
// was actually run against for whatever zone the browser reports right now.
(function () {
    var field = document.querySelector('input[name="tz"]');
    if (field && !field.value) {
        try {
            field.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
        } catch (e) {
            field.value = "UTC";
        }
    }
})();
