"""
Healthcare Admin Domain Schema for Knowledge Graph

Entities: Patient, Provider, Claim, Insurance, Appointment, Prescription, MedicalRecord, PriorAuthorization
Relationships: Treats, Submits, Covers, Prescribes, etc.
Temporal: valid_from, valid_to for all entities and relationships
"""

from typing import Dict, Any

HEALTHCARE_ADMIN_SCHEMA = {
    "entities": {
        "Patient": {
            "properties": {
                "id": "STRING",
                "first_name": "STRING",
                "last_name": "STRING",
                "date_of_birth": "STRING",
                "gender": "STRING",
                "email": "STRING",
                "phone": "STRING",
                "address": "STRING",
                "insurance_id": "STRING",
                "primary_care_provider_id": "STRING",
                "medical_record_number": "STRING",
                "created_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Provider": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "npi": "STRING",  # National Provider Identifier
                "specialty": "STRING",
                "address": "STRING",
                "phone": "STRING",
                "email": "STRING",
                "tax_id": "STRING",
                "facility_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Claim": {
            "properties": {
                "id": "STRING",
                "claim_number": "STRING",
                "patient_id": "STRING",
                "provider_id": "STRING",
                "insurance_id": "STRING",
                "service_date": "STRING",
                "submission_date": "STRING",
                "total_amount": "FLOAT",
                "paid_amount": "FLOAT",
                "denied_amount": "FLOAT",
                "status": "STRING",  # Submitted, Processing, Paid, Denied, Appealed, Rejected
                "denial_reason": "STRING",
                "appeal_status": "STRING",  # Not Appealed, In Progress, Approved, Denied
                "cpt_codes": "STRING",  # Comma-separated CPT codes
                "icd_codes": "STRING",  # Comma-separated ICD-10 codes
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Insurance": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "policy_number": "STRING",
                "group_number": "STRING",
                "payer_id": "STRING",
                "coverage_type": "STRING",  # Medicare, Medicaid, Private, HMO, PPO
                "effective_date": "STRING",
                "termination_date": "STRING",
                "patient_responsibility": "FLOAT",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Appointment": {
            "properties": {
                "id": "STRING",
                "patient_id": "STRING",
                "provider_id": "STRING",
                "scheduled_date": "STRING",
                "start_time": "STRING",
                "end_time": "STRING",
                "type": "STRING",  # Initial, Follow-up, Consultation, Procedure
                "status": "STRING",  # Scheduled, Confirmed, Completed, Cancelled, No-show
                "reason": "STRING",
                "notes": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Prescription": {
            "properties": {
                "id": "STRING",
                "patient_id": "STRING",
                "provider_id": "STRING",
                "medication_name": "STRING",
                "dosage": "STRING",
                "frequency": "STRING",
                "route": "STRING",
                "quantity": "INTEGER",
                "refills": "INTEGER",
                "prescribed_date": "STRING",
                "expiration_date": "STRING",
                "status": "STRING",  # Active, Discontinued, Expired, Filled
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "MedicalRecord": {
            "properties": {
                "id": "STRING",
                "patient_id": "STRING",
                "provider_id": "STRING",
                "record_type": "STRING",  # Lab Report, Imaging, Clinical Note, Summary
                "record_date": "STRING",
                "description": "STRING",
                "file_url": "STRING",
                "test_results": "STRING",
                "diagnosis": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "PriorAuthorization": {
            "properties": {
                "id": "STRING",
                "request_number": "STRING",
                "patient_id": "STRING",
                "provider_id": "STRING",
                "insurance_id": "STRING",
                "service_type": "STRING",
                "request_date": "STRING",
                "decision_date": "STRING",
                "status": "STRING",  # Pending, Approved, Denied, Expired
                "denial_reason": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Billing": {
            "properties": {
                "id": "STRING",
                "patient_id": "STRING",
                "claim_id": "STRING",
                "total_charges": "FLOAT",
                "insurance_payment": "FLOAT",
                "patient_balance": "FLOAT",
                "paid_date": "STRING",
                "due_date": "STRING",
                "status": "STRING",  # Pending, Paid, Overdue, Collection
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        }
    },
    "relationships": {
        "TREATS": {"from": "Provider", "to": "Patient"},
        "SUBMITS_CLAIM": {"from": "Provider", "to": "Claim"},
        "FILES_CLAIM": {"from": "Patient", "to": "Claim"},
        "COVERS": {"from": "Insurance", "to": "Patient"},
        "PAYS_FOR": {"from": "Insurance", "to": "Claim"},
        "PRESCRIBES": {"from": "Provider", "to": "Prescription"},
        "TAKES": {"from": "Patient", "to": "Prescription"},
        "HAS_APPOINTMENT": {"from": "Patient", "to": "Appointment"},
        "SCHEDULES": {"from": "Provider", "to": "Appointment"},
        "HAS_RECORD": {"from": "Patient", "to": "MedicalRecord"},
        "CREATED_RECORD": {"from": "Provider", "to": "MedicalRecord"},
        "REQUIRES_AUTHORIZATION": {"from": "Claim", "to": "PriorAuthorization"},
        "HAS_BILLING": {"from": "Patient", "to": "Billing"},
        "GENERATES_BILLING": {"from": "Claim", "to": "Billing"},
        "APPEALS": {"from": "Claim", "to": "Claim"}  # For claim appeals
    }
}