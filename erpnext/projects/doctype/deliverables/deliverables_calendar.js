// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.views.calendar["Deliverables"] = {
	field_map: {
		"start": "eng_start_date",
		"end": "eng_complete_date",
		"status": "eng_status",
		"id": "name",
		"title": "deliv_title",
		"allDay": "allDay",
		"progress": "progress"
	},
	gantt: true,
	filters: [
		{
			"fieldtype": "Link",
			"fieldname": "deliv_title",
			"options": "Project",
			"label": __("deliv_title")
		}
	],
	get_events_method: "frappe.desk.calendar.get_events"
}
