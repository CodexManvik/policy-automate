"""
External enterprise ledger database seed profiles.
Used by mock gateways endpoints.
"""

POLICY_DATA = {
    "POL-1001": {
        "policy_id": "POL-1001",
        "status": "Active",
        "product_code": "R3",
        "policy_variant": "Classic",
        "policy_start_date": "2025-01-01T00:00:00Z",
        "policy_end_date": "2026-01-01T00:00:00Z",
        "base_sum_insured": 500000.0,
        "optional_riders": ["unlimited_si", "tiered_network"],
        "co_pay_option": 0.10,
        "deductible_option": 0.0,
        "room_category_entitled": "Single Private Room"
    },
    "POL-2002": {
        "policy_id": "POL-2002",
        "status": "Lapsed",
        "product_code": "R3",
        "policy_variant": "Classic",
        "policy_start_date": "2024-01-01T00:00:00Z",
        "policy_end_date": "2025-01-01T00:00:00Z",
        "base_sum_insured": 300000.0,
        "optional_riders": [],
        "co_pay_option": 0.20,
        "deductible_option": 0.0,
        "room_category_entitled": "General Ward"
    },
    "POL-3003": {
        "policy_id": "POL-3003",
        "status": "Active",
        "product_code": "R3",
        "policy_variant": "Select",
        "policy_start_date": "2025-01-01T00:00:00Z",
        "policy_end_date": "2026-01-01T00:00:00Z",
        "base_sum_insured": 750000.0,
        "optional_riders": ["heads_up"],
        "co_pay_option": 0.0,
        "deductible_option": 10000.0,
        "room_category_entitled": "Shared Accommodation"
    },
    "POL-4004": {
        "policy_id": "POL-4004",
        "status": "Active",
        "product_code": "R3",
        "policy_variant": "Elite",
        "policy_start_date": "2025-01-01T00:00:00Z",
        "policy_end_date": "2026-01-01T00:00:00Z",
        "base_sum_insured": 1000000.0,
        "optional_riders": ["unlimited_si", "modern_treatments_plus"],
        "co_pay_option": 0.0,
        "deductible_option": 0.0,
        "room_category_entitled": "Suite"
    }
}

MEMBER_DATA = {
    "MEM-9921": {
        "member_id": "MEM-9921",
        "policy_id": "POL-1001",
        "name": "Jane Doe",
        "age": 35,
        "relationship": "Self",
        "date_of_addition": "2025-01-01T00:00:00Z",
        "ped_declarations": ["Diabetes"],
        "eligibility_active": True
    },
    "MEM-8822": {
        "member_id": "MEM-8822",
        "policy_id": "POL-2002",
        "name": "John Smith",
        "age": 45,
        "relationship": "Self",
        "date_of_addition": "2024-01-01T00:00:00Z",
        "ped_declarations": [],
        "eligibility_active": True
    },
    "MEM-7723": {
        "member_id": "MEM-7723",
        "policy_id": "POL-3003",
        "name": "Robert Johnson",
        "age": 52,
        "relationship": "Self",
        "date_of_addition": "2025-01-01T00:00:00Z",
        "ped_declarations": ["Hypertension"],
        "eligibility_active": True
    },
    "MEM-6624": {
        "member_id": "MEM-6624",
        "policy_id": "POL-4004",
        "name": "Emily Davis",
        "age": 25,
        "relationship": "Self",
        "date_of_addition": "2025-01-01T00:00:00Z",
        "ped_declarations": [],
        "eligibility_active": True
    }
}

CLAIMS_HISTORY_DATA = {
    "POL-1001_MEM-9921": {
        "policy_id": "POL-1001",
        "member_id": "MEM-9921",
        "prior_claims_count": 1,
        "total_prior_amount_paid": 50000.0,
        "cumulative_exclusions_triggered": ["R3_EXCL_003"]
    },
    "POL-2002_MEM-8822": {
        "policy_id": "POL-2002",
        "member_id": "MEM-8822",
        "prior_claims_count": 3,
        "total_prior_amount_paid": 250000.0,
        "cumulative_exclusions_triggered": []
    },
    "POL-3003_MEM-7723": {
        "policy_id": "POL-3003",
        "member_id": "MEM-7723",
        "prior_claims_count": 0,
        "total_prior_amount_paid": 0.0,
        "cumulative_exclusions_triggered": []
    },
    "POL-4004_MEM-6624": {
        "policy_id": "POL-4004",
        "member_id": "MEM-6624",
        "prior_claims_count": 0,
        "total_prior_amount_paid": 0.0,
        "cumulative_exclusions_triggered": []
    }
}

PORTING_DATA = {
    "POL-1001": {
        "policy_id": "POL-1001",
        "is_ported_policy": True,
        "waiting_period_credit_months": 12,
        "moratorium_eligible_months": 0,
        "continuous_coverage_months": 24
    },
    "POL-2002": {
        "policy_id": "POL-2002",
        "is_ported_policy": False,
        "waiting_period_credit_months": 0,
        "moratorium_eligible_months": 0,
        "continuous_coverage_months": 12
    },
    "POL-3003": {
        "policy_id": "POL-3003",
        "is_ported_policy": True,
        "waiting_period_credit_months": 36,
        "moratorium_eligible_months": 0,
        "continuous_coverage_months": 36
    },
    "POL-4004": {
        "policy_id": "POL-4004",
        "is_ported_policy": False,
        "waiting_period_credit_months": 0,
        "moratorium_eligible_months": 0,
        "continuous_coverage_months": 12
    }
}

NETWORK_DATA = {
    "PROV-551": {
        "provider_id": "PROV-551",
        "hospital_name": "City Care Hospital",
        "network_tier": "Network",
        "heads_up_recommended": False
    },
    "PROV-662": {
        "provider_id": "PROV-662",
        "hospital_name": "Metro General Clinic",
        "network_tier": "Tiered",
        "heads_up_recommended": False
    },
    "PROV-773": {
        "provider_id": "PROV-773",
        "hospital_name": "Excluded Health Center",
        "network_tier": "Excluded",
        "heads_up_recommended": False
    },
    "PROV-884": {
        "provider_id": "PROV-884",
        "hospital_name": "Out-of-Network Hospital",
        "network_tier": "Non-Network",
        "heads_up_recommended": True
    }
}

BALANCES_DATA = {
    "POL-1001_MEM-9921": {
        "policy_id": "POL-1001",
        "member_id": "MEM-9921",
        "base_si_remaining": 450000.0,
        "booster_plus_remaining": 50000.0,
        "reassure_forever_pool": 500000.0,
        "cash_bag_plus_wallet_balance": 0.0,
        "hospital_cash_days_used": 2,
        "personal_accident_limit_remaining": 100000.0
    },
    "POL-2002_MEM-8822": {
        "policy_id": "POL-2002",
        "member_id": "MEM-8822",
        "base_si_remaining": 50000.0,
        "booster_plus_remaining": 0.0,
        "reassure_forever_pool": 0.0,
        "cash_bag_plus_wallet_balance": 0.0,
        "hospital_cash_days_used": 0,
        "personal_accident_limit_remaining": 0.0
    },
    "POL-3003_MEM-7723": {
        "policy_id": "POL-3003",
        "member_id": "MEM-7723",
        "base_si_remaining": 750000.0,
        "booster_plus_remaining": 150000.0,
        "reassure_forever_pool": 750000.0,
        "cash_bag_plus_wallet_balance": 5000.0,
        "hospital_cash_days_used": 0,
        "personal_accident_limit_remaining": 150000.0
    },
    "POL-4004_MEM-6624": {
        "policy_id": "POL-4004",
        "member_id": "MEM-6624",
        "base_si_remaining": 1000000.0,
        "booster_plus_remaining": 1000000.0,
        "reassure_forever_pool": 1000000.0,
        "cash_bag_plus_wallet_balance": 25000.0,
        "hospital_cash_days_used": 0,
        "personal_accident_limit_remaining": 500000.0
    }
}

LIFETIME_STATE_DATA = {
    "POL-1001": {
        "policy_id": "POL-1001",
        "lock_the_clock_age_locked": True,
        "current_premium_age": 35,
        "reassure_forever_triggered": False,
        "convalescence_claimed": False,
        "critical_illness_claimed": False,
        "live_healthy": {
            "current_points": 2800,
            "points_snapshot_date": "2025-01-01T00:00:00Z"
        },
        "cash_bag_plus": {
            "balance": 15000.0,
            "last_credited": "2025-04-01T00:00:00Z"
        }
    },
    "POL-2002": {
        "policy_id": "POL-2002",
        "lock_the_clock_age_locked": False,
        "current_premium_age": 45,
        "reassure_forever_triggered": False,
        "convalescence_claimed": True,
        "critical_illness_claimed": False,
        "live_healthy": {
            "current_points": 1200,
            "points_snapshot_date": "2024-12-01T00:00:00Z"
        },
        "cash_bag_plus": {
            "balance": 5000.0,
            "last_credited": "2024-12-01T00:00:00Z"
        }
    },
    "POL-3003": {
        "policy_id": "POL-3003",
        "lock_the_clock_age_locked": True,
        "current_premium_age": 52,
        "reassure_forever_triggered": False,
        "convalescence_claimed": False,
        "critical_illness_claimed": False,
        "live_healthy": {
            "current_points": 3200,
            "points_snapshot_date": "2025-01-01T00:00:00Z"
        },
        "cash_bag_plus": {
            "balance": 8000.0,
            "last_credited": "2025-04-01T00:00:00Z"
        }
    },
    "POL-4004": {
        "policy_id": "POL-4004",
        "lock_the_clock_age_locked": True,
        "current_premium_age": 25,
        "reassure_forever_triggered": False,
        "convalescence_claimed": False,
        "critical_illness_claimed": False,
        "live_healthy": {
            "current_points": 4000,
            "points_snapshot_date": "2025-01-01T00:00:00Z"
        },
        "cash_bag_plus": {
            "balance": 25000.0,
            "last_credited": "2025-04-01T00:00:00Z"
        }
    }
}

ENDORSEMENT_DATA = {
    "POL-1001": {
        "endorsements": [
            {
                "endorsement_id": "END-882",
                "policy_id": "POL-1001",
                "type": "SIEnhancement",
                "mutated_fields": {"base_sum_insured": 500000.0},
                "effective_date": "2025-06-01T00:00:00Z"
            }
        ]
    },
    "POL-2002": {
        "endorsements": []
    },
    "POL-3003": {
        "endorsements": [
            {
                "endorsement_id": "END-301",
                "policy_id": "POL-3003",
                "type": "RiderAddition",
                "mutated_fields": {"optional_riders": ["heads_up"]},
                "effective_date": "2025-02-01T00:00:00Z"
            }
        ]
    },
    "POL-4004": {
        "endorsements": []
    }
}
