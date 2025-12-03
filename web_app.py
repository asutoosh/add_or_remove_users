from datetime import datetime, timezone
from typing import Optional

import os
import requests
from dotenv import load_dotenv
from flask import Flask, request, render_template_string, jsonify

from storage import set_pending_verification, get_pending_verification


# Load .env if present (for droplet: we typically use a .env file in the app directory)
load_dotenv()

IP2LOCATION_API_KEY = os.environ.get("IP2LOCATION_API_KEY", "")
# Blocked country code (e.g., "IN" for India, "PK" for Pakistan)
# Set via environment variable, defaults to "PK" for testing
BLOCKED_COUNTRY_CODE = os.environ.get("BLOCKED_COUNTRY_CODE", "PK").upper()

app = Flask(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_client_ip() -> str:
    """
    Best-effort client IP extraction. If you're behind a reverse proxy
    (Cloudflare, Nginx, etc.) make sure to forward the correct header.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the list
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def _ip2location_lookup(ip: str) -> Optional[dict]:
    """
    Call IP2Location.io to get geolocation and proxy info.
    Docs: https://www.ip2location.io/ip2location-documentation
    """
    base_url = "https://api.ip2location.io/"
    params = {"ip": ip, "format": "json"}
    # If you configured a key, send it; otherwise keyless (limited) mode.
    if IP2LOCATION_API_KEY:
        params["key"] = IP2LOCATION_API_KEY

    try:
        resp = requests.get(base_url, params=params, timeout=3)
        if not resp.ok:
            return None
        data = resp.json()
        # If API returned an error object, treat as no data
        if isinstance(data, dict) and "error" in data:
            return None
        return data
    except Exception:
        # Fail open: if the lookup fails, we won't block user purely on API failure.
        return None


def is_blocked_country_ip(ip: str) -> bool:
    """
    Use IP2Location.io to check if IP country_code matches the blocked country.
    Blocked country is set via BLOCKED_COUNTRY_CODE environment variable.
    """
    data = _ip2location_lookup(ip)
    if not data:
        return False
    country_code = data.get("country_code", "").upper()
    return country_code == BLOCKED_COUNTRY_CODE


def is_vpn_ip(ip: str) -> bool:
    """
    Use IP2Location.io proxy fields to detect VPN / proxy.
    Checks multiple indicators to catch all VPN/proxy types.
    """
    data = _ip2location_lookup(ip)
    if not data:
        # If API lookup fails, be conservative and don't block
        return False

    # Check top-level is_proxy flag (available in all plans)
    # This is the most reliable indicator
    if data.get("is_proxy") is True:
        return True

    # Check detailed proxy object (available in Plus/Security plans)
    proxy = data.get("proxy")
    if proxy and isinstance(proxy, dict):
        # Check all VPN/proxy indicators - any True value means it's a proxy
        proxy_indicators = [
            proxy.get("is_vpn"),
            proxy.get("is_tor"),
            proxy.get("is_public_proxy"),
            proxy.get("is_web_proxy"),
            proxy.get("is_residential_proxy"),
            proxy.get("is_data_center"),
            proxy.get("is_consumer_privacy_network"),
            proxy.get("is_enterprise_private_network"),
            proxy.get("is_web_crawler"),  # Sometimes VPNs are flagged as crawlers
        ]
        # Check if any indicator is True
        if any(proxy_indicators):
            return True

    # Check proxy_type field if present (string value)
    proxy_type = data.get("proxy_type")
    if proxy_type:
        proxy_type_upper = str(proxy_type).upper()
        if proxy_type_upper in ["VPN", "TOR", "PUB", "WEB", "RES", "DCH", "CPN", "EPN", "SES"]:
            return True

    # Check proxy.proxy_type if nested
    if proxy and isinstance(proxy, dict):
        nested_proxy_type = proxy.get("proxy_type")
        if nested_proxy_type:
            nested_type_upper = str(nested_proxy_type).upper()
            if nested_type_upper in ["VPN", "TOR", "PUB", "WEB", "RES", "DCH", "CPN", "EPN", "SES"]:
                return True

    # Additional check: if usage_type suggests datacenter/hosting
    usage_type = data.get("usage_type")
    if usage_type and isinstance(usage_type, str):
        # DCH = Data Center / Hosting (often used by VPNs)
        if "DCH" in usage_type.upper() or "DATACENTER" in usage_type.upper():
            # But don't block if it's clearly a legitimate service
            # Only block if combined with other indicators
            pass

    return False


TRIAL_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Free Trial Verification</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
  {% raw %}
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #050816;
      color: #f3f4f6;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 16px;
    }
    .card {
      background: linear-gradient(145deg, #020617, #020617);
      border-radius: 16px;
      padding: 24px 20px;
      box-shadow: 0 20px 40px rgba(15,23,42,0.8);
      max-width: 420px;
      width: 100%;
      border: 1px solid rgba(148,163,184,0.4);
    }
    h2 {
      margin-top: 0;
      font-size: 1.4rem;
    }
    p {
      font-size: 0.95rem;
      line-height: 1.5;
      color: #e5e7eb;
    }
    form {
      margin-top: 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    label {
      font-size: 0.85rem;
      color: #9ca3af;
    }
    input[type="text"],
    input[type="email"],
    select {
      padding: 9px 10px;
      border-radius: 8px;
      border: 1px solid #4b5563;
      background: rgba(15,23,42,0.8);
      color: #f9fafb;
      font-size: 0.9rem;
      width: 100%;
      box-sizing: border-box;
    }
    select {
      cursor: pointer;
    }
    select option {
      background: #020617;
      color: #f9fafb;
    }
    input[type="checkbox"] {
      margin-right: 6px;
    }
    button {
      margin-top: 6px;
      padding: 10px 12px;
      border-radius: 999px;
      border: none;
      background: linear-gradient(135deg, #22c55e, #16a34a);
      color: white;
      font-weight: 600;
      font-size: 0.95rem;
      cursor: pointer;
      box-shadow: 0 8px 20px rgba(34,197,94,0.45);
    }
    button:active {
      transform: translateY(1px);
      box-shadow: 0 4px 10px rgba(34,197,94,0.3);
    }
    .note {
      font-size: 0.78rem;
      color: #9ca3af;
      margin-top: 10px;
    }
  {% endraw %}
  </style>
</head>
<body>
  <div class="card">
    <h2>Free Trial Verification</h2>
    <p>{{ message }}</p>
    {% if show_form %}
    <form method="post" id="trial-form" onsubmit="return validateForm(event)">
      <input type="hidden" id="tg_id" name="tg_id" value="">
      <!-- Also send as separate field for better reliability -->
      <input type="hidden" id="tg_id_backup" name="tg_id_backup" value="">
      <div id="error-message" style="color: #ef4444; font-size: 0.85rem; margin-bottom: 10px; display: none;"></div>
      <div id="loading-indicator" style="color: #9ca3af; font-size: 0.8rem; margin-bottom: 10px; display: none;">Verifying your Telegram account...</div>
      <div>
        <label for="name">Name</label><br>
        <input id="name" name="name" type="text" required>
      </div>
      <div>
        <label for="country">Country</label><br>
        <select id="country" name="country" required>
          <option value="">Select your country</option>
          <option value="Afghanistan">Afghanistan</option>
          <option value="Albania">Albania</option>
          <option value="Algeria">Algeria</option>
          <option value="Andorra">Andorra</option>
          <option value="Angola">Angola</option>
          <option value="Antigua and Barbuda">Antigua and Barbuda</option>
          <option value="Argentina">Argentina</option>
          <option value="Armenia">Armenia</option>
          <option value="Australia">Australia</option>
          <option value="Austria">Austria</option>
          <option value="Azerbaijan">Azerbaijan</option>
          <option value="Bahamas">Bahamas</option>
          <option value="Bahrain">Bahrain</option>
          <option value="Bangladesh">Bangladesh</option>
          <option value="Barbados">Barbados</option>
          <option value="Belarus">Belarus</option>
          <option value="Belgium">Belgium</option>
          <option value="Belize">Belize</option>
          <option value="Benin">Benin</option>
          <option value="Bhutan">Bhutan</option>
          <option value="Bolivia">Bolivia</option>
          <option value="Bosnia and Herzegovina">Bosnia and Herzegovina</option>
          <option value="Botswana">Botswana</option>
          <option value="Brazil">Brazil</option>
          <option value="Brunei">Brunei</option>
          <option value="Bulgaria">Bulgaria</option>
          <option value="Burkina Faso">Burkina Faso</option>
          <option value="Burundi">Burundi</option>
          <option value="Cambodia">Cambodia</option>
          <option value="Cameroon">Cameroon</option>
          <option value="Canada">Canada</option>
          <option value="Cape Verde">Cape Verde</option>
          <option value="Central African Republic">Central African Republic</option>
          <option value="Chad">Chad</option>
          <option value="Chile">Chile</option>
          <option value="China">China</option>
          <option value="Colombia">Colombia</option>
          <option value="Comoros">Comoros</option>
          <option value="Congo">Congo</option>
          <option value="Costa Rica">Costa Rica</option>
          <option value="Croatia">Croatia</option>
          <option value="Cuba">Cuba</option>
          <option value="Cyprus">Cyprus</option>
          <option value="Czech Republic">Czech Republic</option>
          <option value="Denmark">Denmark</option>
          <option value="Djibouti">Djibouti</option>
          <option value="Dominica">Dominica</option>
          <option value="Dominican Republic">Dominican Republic</option>
          <option value="Ecuador">Ecuador</option>
          <option value="Egypt">Egypt</option>
          <option value="El Salvador">El Salvador</option>
          <option value="Equatorial Guinea">Equatorial Guinea</option>
          <option value="Eritrea">Eritrea</option>
          <option value="Estonia">Estonia</option>
          <option value="Eswatini">Eswatini</option>
          <option value="Ethiopia">Ethiopia</option>
          <option value="Fiji">Fiji</option>
          <option value="Finland">Finland</option>
          <option value="France">France</option>
          <option value="Gabon">Gabon</option>
          <option value="Gambia">Gambia</option>
          <option value="Georgia">Georgia</option>
          <option value="Germany">Germany</option>
          <option value="Ghana">Ghana</option>
          <option value="Greece">Greece</option>
          <option value="Grenada">Grenada</option>
          <option value="Guatemala">Guatemala</option>
          <option value="Guinea">Guinea</option>
          <option value="Guinea-Bissau">Guinea-Bissau</option>
          <option value="Guyana">Guyana</option>
          <option value="Haiti">Haiti</option>
          <option value="Honduras">Honduras</option>
          <option value="Hungary">Hungary</option>
          <option value="Iceland">Iceland</option>
          <option value="Indonesia">Indonesia</option>
          <option value="Iran">Iran</option>
          <option value="Iraq">Iraq</option>
          <option value="Ireland">Ireland</option>
          <option value="Israel">Israel</option>
          <option value="Italy">Italy</option>
          <option value="Jamaica">Jamaica</option>
          <option value="Japan">Japan</option>
          <option value="Jordan">Jordan</option>
          <option value="Kazakhstan">Kazakhstan</option>
          <option value="Kenya">Kenya</option>
          <option value="Kiribati">Kiribati</option>
          <option value="Kosovo">Kosovo</option>
          <option value="Kuwait">Kuwait</option>
          <option value="Kyrgyzstan">Kyrgyzstan</option>
          <option value="Laos">Laos</option>
          <option value="Latvia">Latvia</option>
          <option value="Lebanon">Lebanon</option>
          <option value="Lesotho">Lesotho</option>
          <option value="Liberia">Liberia</option>
          <option value="Libya">Libya</option>
          <option value="Liechtenstein">Liechtenstein</option>
          <option value="Lithuania">Lithuania</option>
          <option value="Luxembourg">Luxembourg</option>
          <option value="Madagascar">Madagascar</option>
          <option value="Malawi">Malawi</option>
          <option value="Malaysia">Malaysia</option>
          <option value="Maldives">Maldives</option>
          <option value="Mali">Mali</option>
          <option value="Malta">Malta</option>
          <option value="Marshall Islands">Marshall Islands</option>
          <option value="Mauritania">Mauritania</option>
          <option value="Mauritius">Mauritius</option>
          <option value="Mexico">Mexico</option>
          <option value="Micronesia">Micronesia</option>
          <option value="Moldova">Moldova</option>
          <option value="Monaco">Monaco</option>
          <option value="Mongolia">Mongolia</option>
          <option value="Montenegro">Montenegro</option>
          <option value="Morocco">Morocco</option>
          <option value="Mozambique">Mozambique</option>
          <option value="Myanmar">Myanmar</option>
          <option value="Namibia">Namibia</option>
          <option value="Nauru">Nauru</option>
          <option value="Nepal">Nepal</option>
          <option value="Netherlands">Netherlands</option>
          <option value="New Zealand">New Zealand</option>
          <option value="Nicaragua">Nicaragua</option>
          <option value="Niger">Niger</option>
          <option value="Nigeria">Nigeria</option>
          <option value="North Korea">North Korea</option>
          <option value="North Macedonia">North Macedonia</option>
          <option value="Norway">Norway</option>
          <option value="Oman">Oman</option>
          <option value="Pakistan">Pakistan</option>
          <option value="Palau">Palau</option>
          <option value="Palestine">Palestine</option>
          <option value="Panama">Panama</option>
          <option value="Papua New Guinea">Papua New Guinea</option>
          <option value="Paraguay">Paraguay</option>
          <option value="Peru">Peru</option>
          <option value="Philippines">Philippines</option>
          <option value="Poland">Poland</option>
          <option value="Portugal">Portugal</option>
          <option value="Qatar">Qatar</option>
          <option value="Romania">Romania</option>
          <option value="Russia">Russia</option>
          <option value="Rwanda">Rwanda</option>
          <option value="Saint Kitts and Nevis">Saint Kitts and Nevis</option>
          <option value="Saint Lucia">Saint Lucia</option>
          <option value="Saint Vincent and the Grenadines">Saint Vincent and the Grenadines</option>
          <option value="Samoa">Samoa</option>
          <option value="San Marino">San Marino</option>
          <option value="Sao Tome and Principe">Sao Tome and Principe</option>
          <option value="Saudi Arabia">Saudi Arabia</option>
          <option value="Senegal">Senegal</option>
          <option value="Serbia">Serbia</option>
          <option value="Seychelles">Seychelles</option>
          <option value="Sierra Leone">Sierra Leone</option>
          <option value="Singapore">Singapore</option>
          <option value="Slovakia">Slovakia</option>
          <option value="Slovenia">Slovenia</option>
          <option value="Solomon Islands">Solomon Islands</option>
          <option value="Somalia">Somalia</option>
          <option value="South Africa">South Africa</option>
          <option value="South Korea">South Korea</option>
          <option value="South Sudan">South Sudan</option>
          <option value="Spain">Spain</option>
          <option value="Sri Lanka">Sri Lanka</option>
          <option value="Sudan">Sudan</option>
          <option value="Suriname">Suriname</option>
          <option value="Sweden">Sweden</option>
          <option value="Switzerland">Switzerland</option>
          <option value="Syria">Syria</option>
          <option value="Taiwan">Taiwan</option>
          <option value="Tajikistan">Tajikistan</option>
          <option value="Tanzania">Tanzania</option>
          <option value="Thailand">Thailand</option>
          <option value="Timor-Leste">Timor-Leste</option>
          <option value="Togo">Togo</option>
          <option value="Tonga">Tonga</option>
          <option value="Trinidad and Tobago">Trinidad and Tobago</option>
          <option value="Tunisia">Tunisia</option>
          <option value="Turkey">Turkey</option>
          <option value="Turkmenistan">Turkmenistan</option>
          <option value="Tuvalu">Tuvalu</option>
          <option value="Uganda">Uganda</option>
          <option value="Ukraine">Ukraine</option>
          <option value="United Arab Emirates">United Arab Emirates</option>
          <option value="United Kingdom">United Kingdom</option>
          <option value="United States">United States</option>
          <option value="Uruguay">Uruguay</option>
          <option value="Uzbekistan">Uzbekistan</option>
          <option value="Vanuatu">Vanuatu</option>
          <option value="Vatican City">Vatican City</option>
          <option value="Venezuela">Venezuela</option>
          <option value="Vietnam">Vietnam</option>
          <option value="Yemen">Yemen</option>
          <option value="Zambia">Zambia</option>
          <option value="Zimbabwe">Zimbabwe</option>
        </select>
      </div>
      <div>
        <label for="email">Email (optional)</label><br>
        <input id="email" name="email" type="email" placeholder="you@example.com">
      </div>
      <div>
        <label>
          <input type="checkbox" name="marketing_opt_in" value="1">
          I agree to receive future updates and offers about this channel.
        </label>
      </div>
      <button type="submit">Submit &amp; continue</button>
      <div class="note">
        We use your name, country and (optionally) email only for verification,
        security and internal analytics. If you tick the box, we may also send
        you future updates. You can request deletion anytime.
      </div>
    </form>
    {% endif %}
  </div>
  <script>
    // Telegram Web App: Extract user ID from Telegram's API
    // This works when opened via Web App button (web_app parameter)
    // Falls back to query param (?tg_id=...) for testing in regular browser
    function extractTelegramUserId() {
      var tgIdInput = document.getElementById('tg_id');
      if (!tgIdInput) return;
      
      var tgId = null;
      
      // Try to get from Telegram Web App API first
      if (window.Telegram && window.Telegram.WebApp) {
        try {
          // Wait for initDataUnsafe to be available
          if (window.Telegram.WebApp.initDataUnsafe) {
            var user = window.Telegram.WebApp.initDataUnsafe.user;
            if (user && user.id) {
              tgId = user.id.toString();
              tgIdInput.value = tgId;
              // Also set backup field
              var backupInput = document.getElementById('tg_id_backup');
              if (backupInput) {
                backupInput.value = tgId;
              }
              // Expand the Web App to full height for better UX
              window.Telegram.WebApp.expand();
              
              // Check if user already passed step1 (after extracting tg_id)
              checkIfAlreadyPassed(tgId);
              return;
            }
          }
        } catch (e) {
          console.log('Telegram Web App API not fully initialized yet');
        }
      }
      
      // Fallback: try to get from URL query parameter (for testing in browser)
      var urlParams = new URLSearchParams(window.location.search);
      var tgIdFromUrl = urlParams.get('tg_id');
      if (tgIdFromUrl) {
        tgId = tgIdFromUrl;
        tgIdInput.value = tgId;
        // Also set backup field
        var backupInput = document.getElementById('tg_id_backup');
        if (backupInput) {
          backupInput.value = tgId;
        }
        checkIfAlreadyPassed(tgId);
      }
    }
    
    // Check if user already passed step1 and show message/close Web App
    function checkIfAlreadyPassed(tgId) {
      if (!tgId) return;
      
      // Check with server if user already passed step1
      fetch('/check-step1?tg_id=' + tgId)
        .then(response => response.json())
        .then(data => {
          if (data.already_passed) {
            // Show message and close Web App
            var card = document.querySelector('.card');
            if (card) {
              card.innerHTML = '<h2>Free Trial Verification</h2><p>✅ You have already passed Step 1 verification!<br><br>Please close this window and tap \'Continue verification\' button in Telegram to proceed with Step 2.</p>';
            }
            
            // Close Web App after 3 seconds
            if (window.Telegram && window.Telegram.WebApp) {
              setTimeout(function() {
                window.Telegram.WebApp.close();
              }, 3000);
            }
          }
        })
        .catch(err => {
          // Silently fail - don't block user if check fails
          console.log('Could not check step1 status:', err);
        });
    }
    
    // Run when page loads - try multiple times to ensure extraction
    function initTelegramId() {
      extractTelegramUserId();
      
      // Keep trying until we get tg_id or timeout
      var attempts = 0;
      var maxAttempts = 20; // Try for up to 10 seconds (20 * 500ms)
      var checkInterval = setInterval(function() {
        var tgIdInput = document.getElementById('tg_id');
        if (tgIdInput && tgIdInput.value) {
          clearInterval(checkInterval);
          console.log('Telegram ID extracted:', tgIdInput.value);
        } else {
          extractTelegramUserId();
          attempts++;
          if (attempts >= maxAttempts) {
            clearInterval(checkInterval);
            console.log('Could not extract Telegram ID after multiple attempts');
          }
        }
      }, 500);
    }
    
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initTelegramId);
    } else {
      initTelegramId();
    }
    
    // Form validation - ensure tg_id is set before submission
    function validateForm(event) {
      if (event) {
        event.preventDefault(); // Prevent immediate submission
      }
      
      var tgIdInput = document.getElementById('tg_id');
      var errorDiv = document.getElementById('error-message');
      var loadingDiv = document.getElementById('loading-indicator');
      
      if (!tgIdInput) {
        if (errorDiv) {
          errorDiv.textContent = 'Form error. Please refresh the page.';
          errorDiv.style.display = 'block';
        }
        return false;
      }
      
      // Show loading indicator
      if (loadingDiv) {
        loadingDiv.style.display = 'block';
      }
      if (errorDiv) {
        errorDiv.style.display = 'none';
      }
      
      // Always try to extract tg_id one more time before submission
      extractTelegramUserId();
      
      // Function to check and submit if ready
      function checkAndSubmit() {
        if (!tgIdInput.value) {
          // Still no tg_id - try one more time
          extractTelegramUserId();
          
          if (!tgIdInput.value) {
            // Failed to get tg_id
            if (loadingDiv) {
              loadingDiv.style.display = 'none';
            }
            if (errorDiv) {
              errorDiv.textContent = 'Unable to verify your Telegram account. Please make sure you opened this page from Telegram and try again.';
              errorDiv.style.display = 'block';
            }
            return false;
          }
        }
        
        // Verify tg_id is a valid number
        if (!/^\\d+$/.test(tgIdInput.value)) {
          if (loadingDiv) {
            loadingDiv.style.display = 'none';
          }
          if (errorDiv) {
            errorDiv.textContent = 'Invalid Telegram account. Please open this page from Telegram.';
            errorDiv.style.display = 'block';
          }
          return false;
        }
        
        // tg_id is valid - submit the form
        if (loadingDiv) {
          loadingDiv.style.display = 'none';
        }
        document.getElementById('trial-form').submit();
        return true;
      }
      
      // Wait a moment for extraction, then check
      setTimeout(checkAndSubmit, 300);
      return false; // Prevent immediate submission
    }
    
    // Also extract tg_id when form is shown (not just on submit)
    // This ensures it's ready before user fills the form
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function() {
        extractTelegramUserId();
        // Keep trying until we get it
        var attempts = 0;
        var checkInterval = setInterval(function() {
          var tgIdInput = document.getElementById('tg_id');
          if (tgIdInput && tgIdInput.value) {
            clearInterval(checkInterval);
          } else {
            extractTelegramUserId();
            attempts++;
            if (attempts > 10) {
              clearInterval(checkInterval);
            }
          }
        }, 500);
      });
    } else {
      extractTelegramUserId();
    }
    
    // Close Web App if user already passed step1
    {% if already_passed %}
    if (window.Telegram && window.Telegram.WebApp) {
      // Close the Web App after showing message
      setTimeout(function() {
        window.Telegram.WebApp.close();
      }, 3000); // Close after 3 seconds
    }
    {% endif %}
  </script>
</body>
</html>
"""


def _render(message: str, show_form: bool, already_passed: bool = False) -> str:
    return render_template_string(TRIAL_PAGE, message=message, show_form=show_form, already_passed=already_passed)


@app.route("/")
def index() -> str:
    """Simple root route for health checks."""
    return "Telegram Trial Verification Service is running. Use /trial endpoint.", 200


@app.route("/check-step1", methods=["GET"])
def check_step1():
    """Check if user already passed step1 - used by JavaScript."""
    tg_id_param: Optional[str] = request.args.get("tg_id")
    if not tg_id_param or not tg_id_param.isdigit():
        return jsonify({"already_passed": False})
    
    existing_data = get_pending_verification(int(tg_id_param))
    if existing_data and existing_data.get("step1_ok"):
        return jsonify({"already_passed": True})
    return jsonify({"already_passed": False})


@app.route("/api/get-verification", methods=["GET"])
def api_get_verification():
    """
    API endpoint for bot to fetch verification data.
    This allows bot to get data from web app even if they're in separate containers.
    """
    tg_id_param: Optional[str] = request.args.get("tg_id")
    if not tg_id_param or not tg_id_param.isdigit():
        return jsonify({"error": "Invalid or missing tg_id"}), 400
    
    data = get_pending_verification(int(tg_id_param))
    if data:
        return jsonify({"success": True, "data": data})
    return jsonify({"success": False, "data": None})


ENABLE_DEBUG_IP = os.environ.get("ENABLE_DEBUG_IP", "0") == "1"


@app.route("/debug-ip")
def debug_ip() -> str:
    """
    Temporary debug endpoint to check what IP2Location API returns.

    For security, this is DISABLED by default in production.
    Set ENABLE_DEBUG_IP=1 in the environment if you explicitly
    want to turn it on (e.g. for debugging from a trusted IP).
    """
    if not ENABLE_DEBUG_IP:
        return "Not found", 404

    ip = get_client_ip()
    data = _ip2location_lookup(ip)

    if not data:
        return f"API lookup failed for IP: {ip}", 500

    # Format response for debugging
    import json
    debug_info = {
        "ip": ip,
        "country_code": data.get("country_code"),
        "country_name": data.get("country_name"),
        "is_proxy": data.get("is_proxy"),
        "proxy": data.get("proxy"),
        "proxy_type": data.get("proxy_type"),
        "usage_type": data.get("usage_type"),
        "full_response": data,
    }

    return f"<pre>{json.dumps(debug_info, indent=2)}</pre>", 200


@app.route("/trial", methods=["GET", "POST"])
def trial() -> str:
    ip = get_client_ip()

    # For GET requests: Allow page to load without tg_id (JavaScript will extract it from Telegram Web App)
    if request.method == "GET":
        # Check if user already passed step1 (from query param or will be extracted by JavaScript)
        tg_id_param: Optional[str] = request.args.get("tg_id")
        if tg_id_param and tg_id_param.isdigit():
            existing_data = get_pending_verification(int(tg_id_param))
            if existing_data and existing_data.get("step1_ok"):
                # User already passed step1 - show message and close Web App
                return _render(
                    "✅ You have already passed Step 1 verification!\n\n"
                    "Please close this window and tap 'Continue verification' button in Telegram to proceed with Step 2.",
                    show_form=False,
                    already_passed=True,  # Flag to trigger close script
                )
        
        # IP / VPN checks happen before showing the form
        if is_vpn_ip(ip):
            return _render(
                "We detected VPN / proxy on your connection. "
                "Please turn it off and apply again. "
                "We store minimal information only for security and abuse prevention.",
                show_form=False,
            )

        if is_blocked_country_ip(ip):
            country_name = "Pakistan" if BLOCKED_COUNTRY_CODE == "PK" else "India" if BLOCKED_COUNTRY_CODE == "IN" else BLOCKED_COUNTRY_CODE
            return _render(
                f"Sorry, you are not eligible for this trial from your region ({country_name}). "
                "We store minimal information only for security and abuse-prevention. "
                "You can request deletion at any time.",
                show_form=False,
            )

        # Allow page to load - JavaScript will extract tg_id from Telegram Web App API
        return _render(
            "IP check passed. Please fill in your name and country to continue.",
            show_form=True,
        )

    # For POST requests: Require tg_id (from form data, query param, or Telegram Web App initData)
    tg_id_param: Optional[str] = request.form.get("tg_id") or request.form.get("tg_id_backup") or request.args.get("tg_id")
    
    # Fallback: Try to extract from Telegram Web App initData if available
    if not tg_id_param or not tg_id_param.isdigit():
        # Check if this is a Telegram Web App request
        init_data = request.headers.get("X-Telegram-Init-Data") or request.form.get("_auth")
        if init_data:
            # Try to parse initData (basic extraction - in production you should validate the hash)
            try:
                import urllib.parse
                parsed = urllib.parse.parse_qs(init_data)
                if "user" in parsed:
                    user_str = parsed["user"][0]
                    import json
                    user_data = json.loads(user_str)
                    if "id" in user_data:
                        tg_id_param = str(user_data["id"])
            except Exception:
                pass
    
    # Final check - if still no tg_id, return helpful error
    if not tg_id_param or not tg_id_param.isdigit():
        return _render(
            "Error: Could not verify your Telegram account. Please make sure you opened this page from Telegram and try again. "
            "If the problem persists, close this window and tap 'Get Free Trial' again.",
            show_form=True,
        )
    
    tg_id = int(tg_id_param)

    # Re-check VPN and India IP on POST (security: prevent bypass)
    if is_vpn_ip(ip):
        return _render(
            "We detected VPN / proxy on your connection. "
            "Please turn it off and apply again. "
            "We store minimal information only for security and abuse prevention.",
            show_form=False,
        )

    if is_blocked_country_ip(ip):
        country_name = "Pakistan" if BLOCKED_COUNTRY_CODE == "PK" else "India" if BLOCKED_COUNTRY_CODE == "IN" else BLOCKED_COUNTRY_CODE
        return _render(
            f"Sorry, you are not eligible for this trial from your region ({country_name}). "
            "We store minimal information only for security and abuse-prevention. "
            "You can request deletion at any time.",
            show_form=False,
        )

    # POST: user submitted form
    name = (request.form.get("name") or "").strip()
    country = (request.form.get("country") or "").strip()
    email = (request.form.get("email") or "").strip()
    marketing_opt_in = request.form.get("marketing_opt_in") == "1"

    if not name or not country:
        return _render(
            "Name and country are required. Please fill the form again.",
            show_form=True,
        )

    info = {
        "name": name,
        "country": country,
        "email": email,
        "ip": ip,
        "marketing_opt_in": marketing_opt_in,
        "step1_ok": True,
        "status": "step1_passed",
        "created_at": _now_utc().isoformat(),
    }
    
    try:
        set_pending_verification(tg_id, info)
        # Verify it was saved
        from storage import get_pending_verification as verify_get
        verified = verify_get(tg_id)
        if not verified or not verified.get("step1_ok"):
            print(f"WARNING: Data saved but verification failed for tg_id={tg_id}")
            print(f"Saved data: {info}")
        else:
            print(f"✅ Successfully saved verification for tg_id={tg_id}, name={name}")
    except Exception as e:
        # Log error but don't crash - return error message to user
        import traceback
        print(f"❌ Error saving verification data for tg_id={tg_id}: {e}")
        print(traceback.format_exc())
        return _render(
            f"Error saving your information. Please try again. Error: {str(e)}",
            show_form=True,
        )

    return _render(
        "Step 1 verification passed ✅. "
        "Please go back to Telegram and tap 'Continue verification'. "
        "We only use your data for verification, security and (if you agreed) updates.",
        show_form=False,
    )


if __name__ == "__main__":
    # Local testing:
    #   python web_app.py
    #   then open http://127.0.0.1:5000/trial?tg_id=12345
    #
    # On a DigitalOcean droplet, we typically run this via systemd
    # and keep the default port 5000, with Nginx proxying HTTPS traffic.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


