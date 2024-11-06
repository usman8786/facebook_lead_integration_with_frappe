# Copyright (c) 2022, Muhammad Usman, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
import json
import requests
from werkzeug.wrappers import Response
from datetime import datetime

@frappe.whitelist(allow_guest=True)
def facebook_lead():
    # Handle GET request from Facebook for webhook verification
    if frappe.local.request.method == 'GET':
        challenge = frappe.request.args.get('hub.challenge')
        mode = frappe.request.args.get('hub.mode')
        fb_verify_token = frappe.request.args.get('hub.verify_token')
        verify_token = frappe.db.get_single_value("Facebook Lead Integration", "verify_token")

        # Check if mode is subscribe and verify token matches
        if mode == 'subscribe' and fb_verify_token == verify_token:  # Replace with your verify token
            return Response(challenge, status=200, content_type="text/plain")
        else:
            return Response('Invalid verify token or mode', status=403, content_type="text/plain")


    # Handle POST request from Facebook for receiving lead data
    if frappe.local.request.method == 'POST':
        try:
            lead_data = json.loads(frappe.request.data.decode('utf-8'))
            frappe.log_error(lead_data, 'Lead Received From Facebook')

            create_lead(lead_data)
            
            # Log success and return proper response
            return Response(json.dumps({"status": "success", "message": "Lead created successfully"}), status=200, content_type="application/json")
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Facebook Lead Error")
            return Response(json.dumps({"status": "error", "message": str(e)}), status=500, content_type="application/json")

    
def exchange_token(short_lived_token, app_id, app_secret):
    url = 'https://graph.facebook.com/v13.0/oauth/access_token'
    params = {
        'grant_type': 'fb_exchange_token',
        'client_id': app_id,
        'client_secret': app_secret,
        'fb_exchange_token': short_lived_token
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        return data.get('access_token')
    else:
        print("Failed to exchange token:", response.json())
        return None

def create_lead(lead_data):
    try:
        #Exhange the token with facebook new access token
        app_id = frappe.db.get_single_value("Facebook Lead Integration", "app_id")
        app_secret = frappe.db.get_single_value("Facebook Lead Integration", "app_secret_id")
        short_lived_token = frappe.db.get_single_value("Facebook Lead Integration", "page_access_token")
        doctype_name = frappe.db.get_single_value("Facebook Lead Integration", "doctype_name")

        if not app_id and not app_secret and not short_lived_token:
            frappe.log_error("Go to the Doctype: Facebook Lead Integration", 'Please complete configuration')

        long_lived_token = exchange_token(short_lived_token, app_id, app_secret)
        if not long_lived_token:
            frappe.log_error("Failed to get long-lived token", 'Failed to get long-lived token')
            return

        # Update token in the Facebook Lead Integration Doctype
        doc = frappe.get_doc("Facebook Lead Integration")
        doc.page_access_token = long_lived_token
        doc.save()

        # Extract relevant fields from the JSON
        entry = lead_data.get('entry')[0] if lead_data.get('entry') else {}
        change = entry.get('changes')[0] if entry.get('changes') else {}
        value = change.get('value') if change else {}

        # Fetching lead data from Facebook API using long-lived token
        url = f"https://graph.facebook.com/{value.get('leadgen_id')}/?access_token={long_lived_token}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
        else:
            frappe.log_error(f"Failed to fetch lead data: {response.text}", "Facebook Lead Error")
            return

        # Extract field data
        field_data = data.get('field_data', [])
        custom_fields = {}
        full_name = ""
        mobile_no = ""
        whatsapp_number = ""

        for field in field_data:
            # Sanitize the field name to remove special characters
            field_name = field.get('name').replace(' ', '_').lower()
            field_name = ''.join(e for e in field_name if e.isalnum() or e == '_')  # Keep only alphanumeric and underscores
            field_value = field.get('values', [''])[0]

            # Capture specific fields like full_name, phone_number, and whatsapp_number
            if field_name == 'full_name':
                full_name = field_value
                continue  # Skip creating new custom field for full_name
            elif field_name == 'phone_number':
                mobile_no = field_value
                continue  # Skip creating new custom field for phone_number
            elif 'whatsapp' in field_name:
                whatsapp_number = field_value
                continue  # Skip creating new custom field for whatsapp-related fields

            # Check if the field already exists in either standard DocField or Custom Field
            if frappe.db.exists('DocField', {'parent': doctype_name, 'fieldname': field_name}) or frappe.db.exists('Custom Field', {'dt': doctype_name, 'fieldname': field_name}):
                custom_fields[field_name] = field_value
            else:
                # Create the custom field if it doesn't exist, but only for non-specified fields
                create_custom_field(doctype_name, field_name)
                custom_fields[field_name] = field_value

        # Prepare the lead object to insert
        lead_object = {
            'doctype': doctype_name,
            'status': doctype_name,
            'lead_name': full_name,
            'mobile_no': mobile_no,  # Add the captured phone number
            'whatsapp_number': whatsapp_number,  # Add the captured WhatsApp number
            'lead_owner': "circlemarketing883@gmail.com",
            'source': "Facebook",
            **custom_fields  # Add all the custom fields dynamically
        }

        # Log and insert the new lead document
        frappe.log_error(lead_object, f"Mapped Facebook Lead Object for our system against Leadgen ID: {value.get('leadgen_id')}")
        new_lead = frappe.get_doc(lead_object)
        new_lead.insert(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Facebook Lead Error")

def create_custom_field(doctype, fieldname):
    custom_fields_insert_after = frappe.db.get_single_value("Facebook Lead Integration", "custom_fields_insert_after")
    # Create a new custom field if it doesn't exist
    frappe.get_doc({
        "doctype": "Custom Field",
        "dt": doctype,
        "label": fieldname.replace("_", " ").title(),
        "fieldname": fieldname,
        "fieldtype": "Data",  # Default field type, can be adjusted
        "insert_after": custom_fields_insert_after  # Adjust position based on your Doctype structure
    }).insert()
    frappe.db.commit()