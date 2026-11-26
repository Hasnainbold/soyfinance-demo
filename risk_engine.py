import numpy as np
import pandas as pd
from datetime import datetime
import hashlib
import random

try:
    from volatility_model import compute_volatility_risk_score
except ImportError:
    # Fallback if module is missing during testing
    def compute_volatility_risk_score(*args, **kwargs):
        return {'score': 50.0, 'details': 'Mocked volatility score due to import error.'}

try:
    from yield_risk_model import compute_crop_yield_risk_score
except ImportError:
    # Fallback if module is missing during testing
    def compute_crop_yield_risk_score(*args, **kwargs):
        return {'score': 50.0, 'details': 'Mocked yield score due to import error.'}

try:
    from storage_shrinkage_model import compute_shrinkage_risk
except ImportError:
    # Fallback if module is missing during testing
    def compute_shrinkage_risk(*args, **kwargs):
        return {
            'score': 30.0, 'estimated_weight_loss_pct': 1.5, 'effective_value_factor': 0.985,
            'details': 'Mocked shrinkage score due to import error.', 'breakdown': {},
        }


def compute_price_level_risk(
    current_price: float, 
    msp: float, 
    predicted_prices: dict = None, 
    deposit_price: float = None, 
    price_history: pd.Series = None
) -> dict:
    """
    Computes price level risk based on current price versus MSP, price history, and forecast.
    
    Sub-scores weighted: 
    - msp_distance (30%)
    - forecast_trend (35%)
    - percentile (20%)
    - deposit_change (15%)
    """
    try:
        # 1. MSP Distance (30%)
        if current_price <= msp * 0.95:
            msp_score = 95.0
        elif current_price <= msp:
            msp_score = 75.0
        elif current_price <= msp * 1.1:
            msp_score = 40.0
        else:
            msp_score = 15.0

        # 2. Forecast Trend (35%)
        forecast_score = 50.0
        if predicted_prices and len(predicted_prices) > 0:
            min_predicted = min(float(v) for v in predicted_prices.values())
            drop = (current_price - min_predicted) / current_price
            if drop > 0.10:
                forecast_score = 90.0
            elif drop > 0.05:
                forecast_score = 65.0
            elif drop > 0.0:
                forecast_score = 40.0
            else:
                forecast_score = 15.0

        # 3. Percentile in 2-year history (20%)
        percentile_score = 50.0
        if price_history is not None and len(price_history) > 0:
            pct = (price_history < current_price).mean()
            if pct > 0.90:
                percentile_score = 85.0
            elif pct > 0.70:
                percentile_score = 60.0
            elif pct > 0.30:
                percentile_score = 35.0
            else:
                percentile_score = 15.0

        # 4. Deposit Change (15%)
        deposit_score = 50.0
        if deposit_price:
            change = (current_price - deposit_price) / deposit_price
            if change < -0.10:
                deposit_score = 90.0
            elif change < -0.05:
                deposit_score = 70.0
            elif change < 0.0:
                deposit_score = 50.0
            else:
                deposit_score = 20.0

        score = (msp_score * 0.30) + (forecast_score * 0.35) + (percentile_score * 0.20) + (deposit_score * 0.15)
        
        return {
            'score': round(min(100.0, max(0.0, score)), 2),
            'details': 'Price level risk computed successfully.',
            'breakdown': {
                'msp_distance': msp_score,
                'forecast_trend': forecast_score,
                'percentile': percentile_score,
                'deposit_change': deposit_score
            }
        }
    except Exception as e:
        return {
            'score': 50.0, 
            'details': f'Error computing price level risk: {str(e)}', 
            'breakdown': {}
        }


def compute_warehouse_risk(receipt: dict) -> dict:
    """
    Computes warehouse risk using receipt metadata.

    Scoring weights:
    - WDRA registration + registry health (30%)
    - Insurance coverage + policy validity + coverage ratio (25%)
    - Storage duration (20%)
    - Collateral manager (15%)
    - Location flood risk (10%)

    New (optional, backward-compatible) fields used when present:
    - wdra_registration_number : str  — presence tightens the WDRA score
    - last_inspection_date     : 'YYYY-MM-DD' — staleness penalty
    - insurance_expiry_date    : 'YYYY-MM-DD' — expired/near-expiry penalty
    - sum_insured              : float — coverage-ratio penalty vs market_value
    """
    try:
        # ── WDRA Registration + registry health (30%) ──
        wdra = bool(receipt.get('wdra_registered', False))
        reg_number = receipt.get('wdra_registration_number')
        last_inspection = receipt.get('last_inspection_date')

        if not wdra:
            wdra_score = 70.0
        elif not reg_number:
            # "Registered" but no registration number on file to verify against
            wdra_score = 25.0
        else:
            wdra_score = 10.0
            if last_inspection:
                try:
                    days_since = (datetime.now() - datetime.strptime(str(last_inspection), "%Y-%m-%d")).days
                    if days_since > 365:
                        wdra_score = 30.0
                    elif days_since > 180:
                        wdra_score = 18.0
                except (ValueError, TypeError):
                    wdra_score = 15.0
            else:
                wdra_score = 20.0  # numbered but no inspection record on file

        # ── Insurance: type + validity + coverage ratio (25%) ──
        insurance = str(receipt.get('insurance_coverage', 'none')).lower()
        if insurance == 'full':
            ins_type_score = 10.0
        elif insurance == 'partial':
            ins_type_score = 40.0
        else:
            ins_type_score = 80.0

        expiry = receipt.get('insurance_expiry_date')
        validity_score = ins_type_score
        is_expired = False
        if insurance != 'none' and expiry:
            try:
                days_to_expiry = (datetime.strptime(str(expiry), "%Y-%m-%d") - datetime.now()).days
                if days_to_expiry < 0:
                    is_expired = True
                    validity_score = 95.0
                elif days_to_expiry < 30:
                    validity_score = 55.0
            except (ValueError, TypeError):
                pass

        sum_insured = receipt.get('sum_insured')
        market_value_ref = receipt.get('market_value')
        coverage_score = ins_type_score
        coverage_ratio = None
        if insurance != 'none' and sum_insured and market_value_ref:
            try:
                coverage_ratio = float(sum_insured) / float(market_value_ref) if float(market_value_ref) else 0.0
                if coverage_ratio < 0.70:
                    coverage_score = 75.0
                elif coverage_ratio < 0.90:
                    coverage_score = 45.0
                else:
                    coverage_score = 15.0
            except (ValueError, TypeError, ZeroDivisionError):
                pass

        ins_score = round((ins_type_score * 0.5) + (validity_score * 0.3) + (coverage_score * 0.2), 2)

        # ── Storage duration (20%) ──
        storage_days = receipt.get('storage_days', 60)
        if storage_days < 60:
            storage_score = 10.0
        elif storage_days <= 120:
            storage_score = 35.0
        elif storage_days <= 180:
            storage_score = 60.0
        else:
            storage_score = 85.0

        # ── Collateral Manager (15%) ──
        cm = receipt.get('has_collateral_manager', False)
        cm_score = 10.0 if cm else 60.0

        # ── Location Flood Risk (10%) ──
        location = receipt.get('location', '').lower()
        flood_risk_map = {
            'indore': 20.0,
            'ujjain': 30.0,
            'dewas': 25.0,
            'bhopal': 35.0,
            'dhar': 15.0
        }
        loc_score = flood_risk_map.get(location, 40.0)

        score = (wdra_score * 0.30) + (ins_score * 0.25) + (storage_score * 0.20) + (cm_score * 0.15) + (loc_score * 0.10)

        return {
            'score': round(min(100.0, max(0.0, score)), 2),
            'details': 'Warehouse risk computed successfully.',
            'breakdown': {
                'wdra_registered': wdra_score,
                'insurance_coverage': ins_score,
                'storage_duration': storage_score,
                'collateral_manager': cm_score,
                'location_risk': loc_score,
                # extra diagnostic detail — additive, does not change existing keys
                'wdra_registration_number': reg_number,
                'insurance_policy_expired': is_expired,
                'insurance_coverage_ratio': round(coverage_ratio, 3) if coverage_ratio is not None else None,
            }
        }
    except Exception as e:
        return {
            'score': 50.0,
            'details': f'Error computing warehouse risk: {str(e)}',
            'breakdown': {}
        }


def compute_quality_parameter_score(
    moisture_pct: float = None,
    oil_content_pct: float = None,
    protein_content_pct: float = None,
    foreign_matter_pct: float = None,
) -> dict:
    """
    Grades the physical quality of a deposited lot against standard Indian
    soybean trade specifications (fair-average-quality benchmarks used in
    private trade / NAFED procurement).

    Any parameter left as None is simply excluded from the average, so a
    partially-filled lab slip (e.g. only moisture + foreign matter recorded)
    still produces a usable score instead of failing.
    """
    subs = {}
    try:
        if moisture_pct is not None:
            m = float(moisture_pct)
            if m <= 10.0: subs['moisture'] = 10.0
            elif m <= 12.0: subs['moisture'] = 30.0
            elif m <= 13.0: subs['moisture'] = 55.0
            else: subs['moisture'] = 85.0

        if foreign_matter_pct is not None:
            f = float(foreign_matter_pct)
            if f <= 1.0: subs['foreign_matter'] = 10.0
            elif f <= 2.0: subs['foreign_matter'] = 30.0
            elif f <= 3.0: subs['foreign_matter'] = 55.0
            else: subs['foreign_matter'] = 85.0

        if oil_content_pct is not None:
            o = float(oil_content_pct)
            if o >= 19.0: subs['oil_content'] = 15.0
            elif o >= 17.0: subs['oil_content'] = 35.0
            else: subs['oil_content'] = 65.0

        if protein_content_pct is not None:
            p = float(protein_content_pct)
            if p >= 38.0: subs['protein_content'] = 15.0
            elif p >= 34.0: subs['protein_content'] = 35.0
            else: subs['protein_content'] = 65.0

        if not subs:
            return {'score': None, 'breakdown': {}, 'details': 'No lab quality parameters supplied.'}

        score = round(sum(subs.values()) / len(subs), 2)
        return {'score': score, 'breakdown': subs,
                'details': f'Computed from {len(subs)}/4 supplied quality parameters.'}
    except Exception as e:
        return {'score': None, 'breakdown': {}, 'details': f'Error scoring quality parameters: {e}'}


def compute_ltv_quality_risk(
    loan_amount: float,
    market_value: float,
    live_price: float,
    quantity: float,
    grade: str,
    total_platform_value: float = None,
    quality_params: dict = None,
    shrinkage_factor: float = 1.0,
) -> dict:
    """
    Computes risk based on LTV, crop quality, and physical weight/shrinkage loss.

    Weights:
    - Initial LTV ratio (30%)
    - Live LTV, shrinkage-adjusted (25%)
    - Grade / lab quality (20%)
    - Margin call distance, shrinkage-adjusted (15%)
    - Weight/shrinkage loss (10%)

    `shrinkage_factor` (0-1, from storage_shrinkage_model) discounts the live
    market value to reflect physical weight already lost in storage — so the
    live LTV and margin-call distance reflect quintals actually recoverable,
    not the quintals originally deposited.
    """
    try:
        shrinkage_factor = float(shrinkage_factor) if shrinkage_factor is not None else 1.0
        shrinkage_factor = min(1.0, max(0.0, shrinkage_factor))

        # Initial LTV (30%)
        ltv_ratio = loan_amount / market_value if market_value else 1.0
        if ltv_ratio > 0.85:
            ltv_score = 95.0
        elif ltv_ratio > 0.75:
            ltv_score = 75.0
        elif ltv_ratio > 0.60:
            ltv_score = 45.0
        else:
            ltv_score = 15.0

        # Live LTV, shrinkage-adjusted (25%)
        live_mktval_gross = live_price * quantity
        live_mktval = live_mktval_gross * shrinkage_factor
        live_ltv = loan_amount / live_mktval if live_mktval else 1.0
        if live_ltv > 0.90:
            live_ltv_score = 95.0
        elif live_ltv > 0.80:
            live_ltv_score = 70.0
        elif live_ltv > 0.65:
            live_ltv_score = 40.0
        else:
            live_ltv_score = 15.0

        # Grade / lab quality (20%) — blends the coarse A/B/C label with
        # finer lab parameters (moisture/oil/protein/foreign matter) when available
        grade_upper = str(grade).strip().upper()
        if grade_upper == 'A':
            grade_label_score = 10.0
        elif grade_upper == 'B':
            grade_label_score = 40.0
        else:
            grade_label_score = 75.0

        qp_result = compute_quality_parameter_score(**(quality_params or {}))
        if qp_result['score'] is not None:
            grade_score = round(0.4 * grade_label_score + 0.6 * qp_result['score'], 2)
        else:
            grade_score = grade_label_score

        # Margin Call Distance, shrinkage-adjusted (15%)
        margin_call_distance = (live_mktval - loan_amount) / loan_amount if loan_amount else 1.0
        if margin_call_distance < 0.05:
            margin_score = 95.0
        elif margin_call_distance < 0.15:
            margin_score = 65.0
        elif margin_call_distance < 0.30:
            margin_score = 35.0
        else:
            margin_score = 10.0

        # Weight / shrinkage loss (10%)
        shrink_loss_pct = round((1.0 - shrinkage_factor) * 100.0, 3)
        if shrink_loss_pct < 1.0:
            shrink_score = 15.0
        elif shrink_loss_pct < 2.5:
            shrink_score = 40.0
        elif shrink_loss_pct < 4.0:
            shrink_score = 65.0
        else:
            shrink_score = 90.0

        score = (
            (ltv_score * 0.30) + (live_ltv_score * 0.25) + (grade_score * 0.20)
            + (margin_score * 0.15) + (shrink_score * 0.10)
        )

        return {
            'score': round(min(100.0, max(0.0, score)), 2),
            'ltv_ratio': round(ltv_ratio, 4),
            'live_ltv': round(live_ltv, 4),
            'margin_call_distance': round(margin_call_distance, 4),
            'shrinkage_loss_pct': shrink_loss_pct,
            'details': 'LTV, quality, and shrinkage risk computed successfully.',
            'breakdown': {
                'initial_ltv': ltv_score,
                'live_ltv': live_ltv_score,
                'grade': grade_score,
                'margin_call_distance': margin_score,
                'weight_shrinkage': shrink_score,
                'quality_parameters': qp_result['breakdown'],
            }
        }
    except Exception as e:
        return {
            'score': 50.0,
            'details': f'Error computing LTV/quality risk: {str(e)}',
            'breakdown': {}
        }


def compute_borrower_risk(borrower: dict) -> dict:
    """
    Computes risk associated with the borrower's profile.
    
    Weights:
    - Success rate (30%)
    - Platform tenure (20%)
    - KYC (20%)
    - FPO member (15%)
    - Default history (15%)
    """
    try:
        # Success Rate (30%)
        total = borrower.get('total_transactions', 0)
        success = borrower.get('successful_transactions', 0)
        if total == 0:
            success_score = 50.0
        else:
            rate = success / total
            if rate > 0.95:
                success_score = 10.0
            elif rate >= 0.80:
                success_score = 30.0
            elif rate >= 0.60:
                success_score = 55.0
            else:
                success_score = 80.0

        # Platform tenure (20%)
        tenure = borrower.get('years_on_platform', 0)
        if tenure > 3:
            tenure_score = 10.0
        elif tenure >= 1:
            tenure_score = 30.0
        else:
            tenure_score = 60.0

        # KYC (20%)
        kyc = borrower.get('kyc_verified', False)
        kyc_score = 10.0 if kyc else 65.0

        # FPO member (15%)
        fpo = borrower.get('is_fpo_member', False)
        fpo_score = 15.0 if fpo else 40.0

        # Default History (15%)
        defaults = borrower.get('past_defaults', 0)
        if defaults == 0:
            default_score = 10.0
        elif defaults == 1:
            default_score = 45.0
        else:
            default_score = 80.0

        score = (success_score * 0.30) + (tenure_score * 0.20) + (kyc_score * 0.20) + (fpo_score * 0.15) + (default_score * 0.15)
        
        return {
            'score': round(min(100.0, max(0.0, score)), 2),
            'details': 'Borrower risk computed successfully.',
            'breakdown': {
                'success_rate': success_score,
                'tenure': tenure_score,
                'kyc': kyc_score,
                'fpo_member': fpo_score,
                'defaults': default_score
            }
        }
    except Exception as e:
        return {
            'score': 50.0,
            'details': f'Error computing borrower risk: {str(e)}',
            'breakdown': {}
        }


def compute_composite_risk_score(
    current_price: float,
    msp: float,
    receipt_data: dict,
    live_price: float,
    predicted_prices: dict = None,
    price_history: pd.Series = None,
    borrower_data: dict = None,
    real_rainfall_deviation_pct: float = None,
    production_signal: dict = None,
) -> dict:
    """
    MAIN engine function that orchestrates all 6 pillars and computes the composite score.

    Optional real-data hooks (both default to None = unchanged behavior):
    - real_rainfall_deviation_pct : a live rainfall-deviation figure (see
      agri_data_sources.compute_district_rainfall_deviation) to use in the
      crop-yield/weather pillar instead of the synthetic per-district dataset.
    - production_signal : dict from agri_data_sources.compute_production_yoy_change
      (India or state-level YoY soybean production change). Not yet folded
      into the weighted score — attached to the result as a transparency
      field (`production_signal`) for the UI, pending calibration against
      real outcomes before it moves any pillar weight.
    """
    try:
        # 1. Ensure borrower data exists
        if borrower_data is None:
            borrower_data = generate_synthetic_borrower()

        # 2. Compute the 6 pillars
        p1 = compute_price_level_risk(
            current_price=current_price,
            msp=msp,
            predicted_prices=predicted_prices,
            deposit_price=receipt_data.get('deposit_price', current_price),
            price_history=price_history
        )
        
        try:
            p2 = compute_volatility_risk_score(price_history) if price_history is not None else {'score': 45.0, 'details': 'No history'}
        except Exception:
            p2 = {'score': 50.0, 'details': 'Fallback volatility'}
            
        p3 = compute_warehouse_risk(receipt_data)

        # Weight/shrinkage estimate — feeds the LTV/Quality pillar (adjusts
        # live market value & margin-call distance to reflect quintals
        # actually recoverable, not quintals originally deposited)
        try:
            shrink_result = compute_shrinkage_risk(
                moisture_pct=receipt_data.get('moisture_level', receipt_data.get('moisture_pct', 11.0)),
                storage_days=receipt_data.get('storage_days', 60),
                wdra_registered=receipt_data.get('wdra_registered', True),
                has_collateral_manager=receipt_data.get('has_collateral_manager', True),
            )
        except Exception:
            shrink_result = {'score': 30.0, 'estimated_weight_loss_pct': 1.5,
                              'effective_value_factor': 0.985, 'breakdown': {}}

        quality_params = {
            'moisture_pct': receipt_data.get('moisture_level', receipt_data.get('moisture_pct')),
            'oil_content_pct': receipt_data.get('oil_content_pct'),
            'protein_content_pct': receipt_data.get('protein_content_pct'),
            'foreign_matter_pct': receipt_data.get('foreign_matter_pct'),
        }
        quality_params = {k: v for k, v in quality_params.items() if v is not None}

        p4 = compute_ltv_quality_risk(
            loan_amount=receipt_data.get('loan_amount', 0.0),
            market_value=receipt_data.get('market_value', 1.0),
            live_price=live_price,
            quantity=receipt_data.get('quantity', 1.0),
            grade=receipt_data.get('grade', 'B'),
            quality_params=quality_params,
            shrinkage_factor=shrink_result.get('effective_value_factor', 1.0),
        )

        try:
            p5 = compute_crop_yield_risk_score(
                receipt_data.get('location', 'unknown'),
                real_rainfall_deviation_pct=real_rainfall_deviation_pct,
            )
        except Exception:
            p5 = {'score': 50.0, 'details': 'Fallback yield score'}
            
        p6 = compute_borrower_risk(borrower_data)

        # 3. Apply pillar weights
        weights = {
            'price_level': 0.20,
            'volatility': 0.25,
            'warehouse': 0.15,
            'ltv_quality': 0.15,
            'crop_yield': 0.15,
            'borrower': 0.10
        }

        composite_score = (
            p1.get('score', 50.0) * weights['price_level'] +
            p2.get('score', 50.0) * weights['volatility'] +
            p3.get('score', 50.0) * weights['warehouse'] +
            p4.get('score', 50.0) * weights['ltv_quality'] +
            p5.get('score', 50.0) * weights['crop_yield'] +
            p6.get('score', 50.0) * weights['borrower']
        )
        composite_score = round(min(100.0, max(0.0, composite_score)), 2)

        # 4. Determine Grades and Recommendations
        if composite_score <= 25.0:
            risk_grade = 'Low'
            recommendation = 'Auto-Approve'
            interest = '10-12%'
        elif composite_score <= 50.0:
            risk_grade = 'Medium'
            recommendation = 'Standard Review'
            interest = '13-15%'
        elif composite_score <= 75.0:
            risk_grade = 'Elevated'
            recommendation = 'Enhanced Diligence'
            interest = '15-17%'
        else:
            risk_grade = 'High'
            recommendation = 'Reject / Additional Collateral'
            interest = '18%+'

        # 5. Extract top risk factors & mitigants
        pillar_scores_map = {
            'Price Level Risk': p1.get('score', 50.0),
            'Volatility Risk': p2.get('score', 50.0),
            'Warehouse/Storage Risk': p3.get('score', 50.0),
            'LTV/Quality Risk': p4.get('score', 50.0),
            'Crop Yield/Weather Risk': p5.get('score', 50.0),
            'Borrower Profile Risk': p6.get('score', 50.0)
        }
        
        sorted_pillars = sorted(pillar_scores_map.items(), key=lambda x: x[1], reverse=True)
        risk_factors = [f[0] for f in sorted_pillars[:3]]
        risk_mitigants = [f[0] for f in sorted_pillars[-3:]]

        return {
            'composite_score': composite_score,
            'risk_grade': risk_grade,
            'recommendation': recommendation,
            'suggested_interest_range': interest,
            'pillar_scores': {
                # 'raw' carries each pillar function's FULL return dict (score,
                # breakdown, and any pillar-specific fields like var_95/regime
                # for volatility or yield_instability/rainfall_risk for crop
                # yield) so the UI can render full detail without re-deriving it.
                'price_level': {'score': p1.get('score', 50.0), 'weight': weights['price_level'], 'weighted_score': round(p1.get('score', 50.0)*weights['price_level'], 2), 'details': p1.get('details', ''), 'raw': p1},
                'volatility': {'score': p2.get('score', 50.0), 'weight': weights['volatility'], 'weighted_score': round(p2.get('score', 50.0)*weights['volatility'], 2), 'details': p2.get('details', ''), 'raw': p2},
                'warehouse': {'score': p3.get('score', 50.0), 'weight': weights['warehouse'], 'weighted_score': round(p3.get('score', 50.0)*weights['warehouse'], 2), 'details': p3.get('details', ''), 'raw': p3},
                'ltv_quality': {'score': p4.get('score', 50.0), 'weight': weights['ltv_quality'], 'weighted_score': round(p4.get('score', 50.0)*weights['ltv_quality'], 2), 'details': p4.get('details', ''), 'raw': p4},
                'crop_yield': {'score': p5.get('score', 50.0), 'weight': weights['crop_yield'], 'weighted_score': round(p5.get('score', 50.0)*weights['crop_yield'], 2), 'details': p5.get('details', ''), 'raw': p5},
                'borrower': {'score': p6.get('score', 50.0), 'weight': weights['borrower'], 'weighted_score': round(p6.get('score', 50.0)*weights['borrower'], 2), 'details': p6.get('details', ''), 'raw': p6}
            },
            'risk_factors': risk_factors,
            'risk_mitigants': risk_mitigants,
            'shrinkage_estimate': shrink_result,
            'production_signal': production_signal,
        }
    except Exception as e:
        return {
            'composite_score': 50.0,
            'risk_grade': 'Medium',
            'recommendation': 'Standard Review (Error Fallback)',
            'suggested_interest_range': '13-15%',
            'error': str(e)
        }


def generate_synthetic_borrower(seed: int = None) -> dict:
    """
    Generates a realistic synthetic borrower profile.
    """
    try:
        rng = random.Random(seed) if seed is not None else random
        
        transactions = rng.randint(1, 50)
        success_rate = rng.uniform(0.7, 1.0)
        
        return {
            'farmer_name': f"Farmer_{rng.randint(1000, 9999)}",
            'total_transactions': transactions,
            'successful_transactions': int(transactions * success_rate),
            'years_on_platform': rng.randint(0, 10),
            'kyc_verified': rng.random() > 0.1,  # 90% chance verified
            'is_fpo_member': rng.random() > 0.4, # 60% chance member
            'past_defaults': rng.choices([0, 1, 2, 3], weights=[0.8, 0.15, 0.04, 0.01])[0]
        }
    except Exception:
        return {
            'farmer_name': "Fallback_Farmer",
            'total_transactions': 10,
            'successful_transactions': 9,
            'years_on_platform': 2,
            'kyc_verified': True,
            'is_fpo_member': False,
            'past_defaults': 0
        }


def generate_enhanced_receipt(
    receipt_id: str, 
    farmer_name: str, 
    location: str, 
    quantity: float, 
    grade: str, 
    deposit_date: str, 
    market_value: float, 
    loan_amount: float, 
    current_price: float, 
    seed: int = None
) -> dict:
    """
    Takes basic receipt info and enhances it with synthetic risk metadata.
    Deterministic if seed is derived from receipt_id.
    """
    try:
        if seed is None:
            # Hash receipt_id to generate a stable seed
            seed = int(hashlib.md5(receipt_id.encode('utf-8')).hexdigest(), 16) % (2**32)
            
        rng = random.Random(seed)
        
        # Calculate storage days
        try:
            dep_date_obj = datetime.strptime(deposit_date, "%Y-%m-%d")
            storage_days = (datetime.now() - dep_date_obj).days
            storage_days = max(0, storage_days)
        except ValueError:
            storage_days = 30
            
        deposit_price = market_value / quantity if quantity > 0 else current_price
        
        enhanced = {
            'receipt_id': receipt_id,
            'farmer_name': farmer_name,
            'location': location,
            'quantity': quantity,
            'grade': grade,
            'deposit_date': deposit_date,
            'market_value': market_value,
            'loan_amount': loan_amount,
            'current_price': current_price,
            'deposit_price': deposit_price,
            
            # Synthetic fields
            'wdra_registered': rng.random() > 0.3, # 70% chance registered
            'insurance_coverage': rng.choice(['full', 'full', 'partial', 'none']),
            'has_collateral_manager': rng.random() > 0.2, # 80% chance
            'storage_days': storage_days,
            'moisture_level': round(rng.uniform(8.0, 14.0), 1),
            'pest_inspection_date': deposit_date
        }
        
        return enhanced
    except Exception as e:
        return {
            'receipt_id': receipt_id,
            'farmer_name': farmer_name,
            'error': str(e)
        }
