import { useState, useEffect, lazy, Suspense, Component } from 'react'

const BarcodeScanner = lazy(() => import('./BarcodeScanner'))
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

class ScannerErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }
  static getDerivedStateFromError() {
    return { hasError: true }
  }
  componentDidCatch(error, info) {
    console.error('BarcodeScanner error caught by boundary:', error, info)
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="scanner-modal-backdrop" role="dialog" onClick={this.props.onClose}>
          <div className="scanner-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="scanner-modal-header">
              <h3 style={{ margin: 0, fontSize: '15px', color: '#fff' }}>Camera Unavailable</h3>
              <button type="button" className="scanner-close-btn" onClick={this.props.onClose}>✕</button>
            </div>
            <div style={{ padding: '24px 20px', textAlign: 'center' }}>
              <p style={{ color: '#ff9b9b', fontSize: '13.5px', marginBottom: '16px' }}>
                Your device or browser encountered an issue accessing the camera stream.
              </p>
              <button
                type="button"
                className="scanner-cancel-btn"
                style={{ width: '100%', minHeight: '44px' }}
                onClick={this.props.onClose}
              >
                Close & Enter Barcode Manually
              </button>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

const TIER_LABELS = {
  avoid: 'Avoid',
  caution: 'Caution',
  moderate: 'Moderate',
  suitable: 'Suitable',
  recommended: 'Recommended',
  unknown: 'Unknown',
}

// One plain sentence explaining what each tier actually means -- the
// point isn't just to name the tier, it's to say what to take away from it.
const TIER_SUMMARIES = {
  avoid:
    "This doesn't fit your goal. At least one thing that has to pass, didn't.",
  caution:
    "We couldn't confirm the main factor, but something else is worth noting.",
  moderate:
    "This mostly fits, but there's a factor worth keeping in moderation.",
  suitable: 'This fits your goal.',
  recommended: 'This is a strong fit for your goal.',
  unknown: "We don't have enough data on this food for a confident answer.",
}

// What each rule role actually means for the verdict, shown once per
// group so the reasoning behind the tier is explained, not just listed.
const ROLE_INFO = {
  blocking: {
    heading: 'Has to pass',
    explain: 'If any of these fail, the verdict is Avoid, regardless of anything else.',
  },
  moderation: {
    heading: 'Worth watching',
    explain: "These don't rule the food out on their own, but a failure here means eating it in moderation.",
  },
  bonus: {
    heading: 'Bonus points',
    explain: "Meeting these can upgrade the verdict to Recommended. Missing them doesn't count against you.",
  },
}

const ROLE_ORDER = ['blocking', 'moderation', 'bonus']

// Order for the visual scale, worst to best. "unknown" is deliberately
// excluded -- it's not a position on a quality scale, it means there
// wasn't enough data to place the food on the scale at all.
const SCALE_TIERS = ['avoid', 'caution', 'moderate', 'suitable', 'recommended']

function statusFor(rule) {
  if (!rule.applicable) return { word: 'Not applicable', kind: 'muted' }
  if (rule.passed === null || rule.passed === undefined) return { word: 'Unknown', kind: 'muted' }
  if (rule.passed) return { word: rule.comparison === 'max' ? 'Within limit' : 'Meets target', kind: 'good' }
  return { word: rule.comparison === 'max' ? 'Over limit' : 'Below target', kind: 'bad' }
}

function limitPhrase(rule) {
  if (rule.threshold === null || rule.threshold === undefined) return ''
  return rule.comparison === 'max'
    ? `Limit: ${rule.threshold}${rule.unit} or less`
    : `Target: ${rule.threshold}${rule.unit} or more`
}

// Shared between the combined view and each individual goal's breakdown --
// grouping rules by role and rendering each with a plain-language status.
function RuleGroups({ rules }) {
  return ROLE_ORDER.filter((role) => rules.some((r) => r.role === role)).map((role) => (
    <div key={role} className="rule-group">
      <div className="rule-group-heading">
        <h4>{ROLE_INFO[role].heading}</h4>
        <p>{ROLE_INFO[role].explain}</p>
      </div>
      <ul className="rules-list">
        {rules
          .filter((r) => r.role === role)
          .map((r, i) => {
            const status = statusFor(r)
            return (
              <li key={i} className={!r.applicable ? 'not-applicable' : ''}>
                <div className="rule-row-top">
                  <span className="rule-label">{r.label}</span>
                  <span className={`status-pill status-${status.kind}`}>{status.word}</span>
                </div>
                {r.applicable && r.value !== null && r.value !== undefined ? (
                  <p className="rule-detail">
                    {r.value}
                    {r.unit} · {limitPhrase(r)}
                    {r.confidence === 'estimated' && ' · estimated from a related nutrient'}
                  </p>
                ) : (
                  <p className="rule-detail muted">
                    {!r.applicable ? "Doesn't apply to this food" : 'No data available for this food'}
                  </p>
                )}
              </li>
            )
          })}
      </ul>
    </div>
  ))
}

function App() {
  const [mode, setMode] = useState('name') // 'name' | 'barcode'
  const [query, setQuery] = useState('')
  const [barcode, setBarcode] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [selectedFood, setSelectedFood] = useState(null)
  const [goals, setGoals] = useState([])
  const [selectedGoals, setSelectedGoals] = useState([])
  const [evaluation, setEvaluation] = useState(null)
  const [tip, setTip] = useState(null)
  const [tipLoading, setTipLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searchPage, setSearchPage] = useState(1)
  const [hasMoreResults, setHasMoreResults] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [isScannerOpen, setIsScannerOpen] = useState(false)

  useEffect(() => {
    fetch(`${API_BASE}/goals`)
      .then((r) => r.json())
      .then(setGoals)
      .catch(() => setError('Could not reach the API. Is the backend running on port 8000?'))
  }, [])

  function reset() {
    setSelectedFood(null)
    setEvaluation(null)
    setTip(null)
    setTipLoading(false)
    setSearchResults([])
    setSelectedGoals([])
    setSearchPage(1)
    setHasMoreResults(false)
    setLoadingMore(false)
    setIsScannerOpen(false)
    setError('')
  }

  function toggleGoal(goalName) {
    setSelectedGoals((prev) =>
      prev.includes(goalName) ? prev.filter((g) => g !== goalName) : [...prev, goalName]
    )
  }

  async function handleSearch(pageToFetch = 1) {
    if (!query.trim()) return
    if (pageToFetch === 1) {
      setLoading(true)
      setSearchResults([])
    } else {
      setLoadingMore(true)
    }
    setError('')
    try {
      const res = await fetch(
        `${API_BASE}/foods/search?q=${encodeURIComponent(query)}&page=${pageToFetch}&page_size=10`
      )
      if (!res.ok) throw new Error('Search failed')
      const data = await res.json()
      if (data.length === 0 && pageToFetch === 1) {
        setError(`No results for "${query}".`)
      }
      if (pageToFetch === 1) {
        setSearchResults(data)
      } else {
        setSearchResults((prev) => [...prev, ...data])
      }
      setSearchPage(pageToFetch)
      setHasMoreResults(data.length === 10)
      setSelectedFood(null)
      setEvaluation(null)
      setTip(null)
    } catch {
      setError('Search failed. Check your connection and try again.')
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }

  async function handleBarcodeLookup(codeToLookup) {
    const code = (typeof codeToLookup === 'string' ? codeToLookup : barcode).trim()
    if (!code) return
    setBarcode(code)
    setIsScannerOpen(false)
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/foods/barcode/${encodeURIComponent(code)}`)
      if (!res.ok) {
        setError(`No product found for barcode "${code}". Try another barcode or search by name.`)
        return
      }
      const data = await res.json()
      setSelectedFood(data)
      setSearchResults([])
      setEvaluation(null)
      setTip(null)
    } catch {
      setError('Lookup failed. Check your connection and try again.')
    } finally {
      setLoading(false)
    }
  }

  async function fetchTip(food, goalNames) {
    setTipLoading(true)
    setTip(null)
    try {
      const res = await fetch(`${API_BASE}/tip`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ food, goal_names: goalNames }),
      })
      if (!res.ok) throw new Error('Tip request failed')
      const tipData = await res.json()
      setTip(tipData)
    } catch {
      // Tip is an asynchronous enhancement -- never breaks core evaluation display
    } finally {
      setTipLoading(false)
    }
  }

  async function handleEvaluate() {
    if (!selectedFood || selectedGoals.length === 0) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/evaluate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ food: selectedFood, goal_names: selectedGoals }),
      })
      if (!res.ok) throw new Error('Evaluation failed')
      const data = await res.json()
      setEvaluation(data)
      // Asynchronously request the grounded contextual tip without blocking verdict rendering
      fetchTip(selectedFood, selectedGoals)
    } catch {
      setError('Evaluation failed. Check your connection and try again.')
    } finally {
      setLoading(false)
    }
  }

  // Single source of truth for "what tier represents this evaluation" --
  // the combined tier when multiple goals were checked, or the one
  // goal's own tier otherwise. Used by the scale marker down in the
  // About section.
  const displayTier = evaluation
    ? evaluation.combined_tier || evaluation.results[0].tier
    : null

  return (
    <div className="page">
      <header className="topbar">
        <span className="brand">MacroVerdict</span>
        <a
          className="repo-link"
          href="https://github.com/mohammadr33/MacroVerdict"
          target="_blank"
          rel="noreferrer"
        >
          Source
        </a>
      </header>

      <section className="hero">
        <h1>Check a food against your goal.</h1>
        <p className="subhead">
          Search a product or scan a barcode. Pick a goal. Get a straight
          answer with the reasons behind it — not just yes or no.
        </p>
      </section>

      <section className="demo" aria-label="Demo">
        <div className="server-notice" role="note">
          <span className="server-notice-icon">⏱️</span>
          <div className="server-notice-content">
            <strong>Server Spin-Up Notice:</strong> MacroVerdict runs on Render’s free tier, which puts the server to sleep after periods of inactivity. If the app hasn’t been used in a while, your first search or barcode lookup may take <strong>around 30 seconds</strong> to wake up. Once awake, all requests are fast!
          </div>
        </div>

        <div className="mode-toggle" role="tablist">
          <button
            role="tab"
            aria-selected={mode === 'name'}
            className={mode === 'name' ? 'active' : ''}
            onClick={() => {
              setMode('name')
              reset()
            }}
          >
            Search by name
          </button>
          <button
            role="tab"
            aria-selected={mode === 'barcode'}
            className={mode === 'barcode' ? 'active' : ''}
            onClick={() => {
              setMode('barcode')
              reset()
            }}
          >
            Scan a barcode
          </button>
        </div>

        {mode === 'name' ? (
          <div className="search-row">
            <input
              type="text"
              value={query}
              placeholder="e.g. granola bar"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              aria-label="Search for a food by name"
            />
            <button onClick={handleSearch} disabled={loading}>
              {loading ? 'Searching…' : 'Search'}
            </button>
          </div>
        ) : (
          <div className="barcode-input-section">
            <div className="barcode-notice-card" role="note">
              <div className="barcode-notice-header">
                <span className="barcode-notice-badge">Camera Notice</span>
                <span className="barcode-notice-sub">Autofocus issue (Work in progress)</span>
              </div>
              <p className="barcode-notice-text">
                Live webcam and browser autofocus may struggle to focus cleanly on some barcodes. If your item isn't scanning, <strong>take a picture of the barcode with your phone</strong> and use the <strong>“Upload Barcode Photo”</strong> option inside the scanner, or type the barcode numbers directly below.
              </p>
            </div>

            <div className="barcode-camera-trigger">
              <button
                type="button"
                className="camera-scan-btn"
                onClick={() => {
                  setError('')
                  setIsScannerOpen(true)
                }}
                disabled={loading}
              >
                <span className="camera-btn-icon" aria-hidden="true">📷</span>
                <div className="camera-btn-text">
                  <strong>Open Camera Scanner</strong>
                  <span>Scan UPC/EAN barcode directly with your device camera</span>
                </div>
              </button>
            </div>

            <div className="barcode-divider">
              <span>or enter barcode number</span>
            </div>

            <div className="search-row">
              <input
                type="text"
                value={barcode}
                placeholder="e.g. 3017620422003 (Nutella)"
                onChange={(e) => setBarcode(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleBarcodeLookup()}
                aria-label="Enter a product barcode"
              />
              <button onClick={() => handleBarcodeLookup()} disabled={loading || !barcode.trim()}>
                {loading ? 'Looking up…' : 'Look up'}
              </button>
            </div>
          </div>
        )}

        {loading && (
          <div className="server-loading-status" role="status" aria-live="polite">
            <span className="spinner-inline" />
            <span>Connecting to database… (If backend is waking up from idle, this may take ~30 seconds)</span>
          </div>
        )}

        {isScannerOpen && (
          <ScannerErrorBoundary onClose={() => setIsScannerOpen(false)}>
            <Suspense
              fallback={
                <div className="scanner-modal-backdrop">
                  <div className="scanner-modal-card">
                    <div className="scanner-status-overlay">
                      <div className="scanner-spinner" />
                      <p>Loading scanner module…</p>
                    </div>
                  </div>
                </div>
              }
            >
              <BarcodeScanner
                onScan={(scannedCode) => {
                  setIsScannerOpen(false)
                  handleBarcodeLookup(scannedCode)
                }}
                onClose={() => setIsScannerOpen(false)}
              />
            </Suspense>
          </ScannerErrorBoundary>
        )}

        {error && <p className="error-text" role="alert">{error}</p>}

        {searchResults.length > 0 && !selectedFood && (
          <div className="results-container">
            <ul className="results-list">
              {searchResults.map((f, i) => (
                <li key={i}>
                  <button
                    onClick={() => {
                      setSelectedFood(f)
                      setEvaluation(null)
                    }}
                  >
                    <div className="result-item-main">
                      <span className="result-food-name">{f.name}</span>
                      {f.is_processed && (
                        <span className="result-processed-pill">Processed</span>
                      )}
                    </div>
                    {f.food_category && (
                      <span className="result-category-sub">{f.food_category}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
            {hasMoreResults && (
              <button
                className="load-more-btn"
                onClick={() => handleSearch(searchPage + 1)}
                disabled={loadingMore}
              >
                {loadingMore ? 'Loading more options…' : 'Load more options ↓'}
              </button>
            )}
          </div>
        )}

        {selectedFood && (
          <div className="selected-food">
            <div className="selected-food-header">
              <h3>{selectedFood.name}</h3>
              <button className="ghost-btn" onClick={reset}>
                Change food
              </button>
            </div>

            <p className="goal-picker-label">
              Pick one or more goals — checking multiple at once shows the
              worst-case verdict across all of them.
            </p>
            <div className="goal-checklist" role="group" aria-label="Pick health goals">
              {goals.map((g) => (
                <label key={g} className="goal-checkbox">
                  <input
                    type="checkbox"
                    checked={selectedGoals.includes(g)}
                    onChange={() => toggleGoal(g)}
                  />
                  {g}
                </label>
              ))}
            </div>
            <button
              className="check-btn"
              onClick={handleEvaluate}
              disabled={selectedGoals.length === 0 || loading}
            >
              {loading ? 'Checking…' : 'Check'}
            </button>
          </div>
        )}

        {evaluation && (
          <div
            className={`verdict-panel tier-${evaluation.combined_tier || evaluation.results[0].tier
              }`}
          >
            {evaluation.results.length > 1 ? (
              <>
                <div className="verdict-heading">
                  <span className="verdict-eyebrow">Overall verdict</span>
                  <span className="verdict-tier">
                    {TIER_LABELS[evaluation.combined_tier] || evaluation.combined_tier}
                  </span>
                </div>
                <p className="verdict-summary">
                  {TIER_SUMMARIES[evaluation.combined_tier]}
                </p>
                <p className="verdict-meta">
                  {evaluation.food_name} · driven by your{' '}
                  <strong>{evaluation.driving_goal}</strong> goal · checked against{' '}
                  {evaluation.results.length} goals
                </p>
              </>
            ) : (
              <>
                <div className="verdict-heading">
                  <span className="verdict-eyebrow">Verdict</span>
                  <span className="verdict-tier">
                    {TIER_LABELS[evaluation.results[0].tier] || evaluation.results[0].tier}
                  </span>
                </div>
                <p className="verdict-summary">
                  {TIER_SUMMARIES[evaluation.results[0].tier]}
                </p>
                <p className="verdict-meta">
                  {evaluation.food_name} · {evaluation.results[0].goal_name}
                </p>
              </>
            )}

            {/* Processed Food Alert Banner */}
            {(evaluation.is_processed || selectedFood?.is_processed) && (
              <div className="processed-food-banner" role="status">
                <div className="processed-badge-header">
                  <span className="processed-pill">Processed Food Alert</span>
                  <span className="processed-nova-note">NOVA 4 / Branded</span>
                </div>
                <p className="processed-banner-text">
                  This item is identified as industrially processed. Processing frequently concentrates saturated fat, sodium, or added sugars while stripping dietary fiber. For your active goal{selectedGoals.length > 1 ? 's' : ''}, prioritizing whole or minimally processed foods promotes higher satiety and cleaner metabolic impact.
                </p>
              </div>
            )}

            {/* Grounded Contextual Tip Card */}
            {(tipLoading || tip) && (
              <div className="contextual-tip-card">
                <div className="tip-header">
                  <div className="tip-title-wrap">
                    <span className="tip-sparkle-icon">✨</span>
                    <span className="tip-title">Grounded Nutritional Insight</span>
                  </div>
                  {tip && (
                    <span className={`tip-badge ${tip.is_ai_generated ? 'badge-ai' : 'badge-fallback'}`}>
                      {tip.source_model || (tip.is_ai_generated ? 'MacroVerdict AI' : 'Rule Synthesizer')}
                    </span>
                  )}
                </div>

                {tipLoading ? (
                  <div className="tip-skeleton-wrap" aria-label="Synthesizing grounded insight...">
                    <div className="tip-skeleton-line full" />
                    <div className="tip-skeleton-line short" />
                  </div>
                ) : (
                  tip && (
                    <div className="tip-body">
                      <p className="tip-takeaway">{tip.takeaway}</p>
                      {tip.suggested_swap && (
                        <div className="tip-swap-callout">
                          <span className="tip-swap-icon">💡</span>
                          <span className="tip-swap-text">{tip.suggested_swap}</span>
                        </div>
                      )}
                    </div>
                  )
                )}
              </div>
            )}

            <div className="verdict-divider" />

            {evaluation.results.length > 1 ? (
              <div className="per-goal-breakdown">
                <h4 className="per-goal-heading">By goal</h4>
                {evaluation.results.map((goalResult) => (
                  <div key={goalResult.goal_name} className="per-goal-block">
                    <div className={`per-goal-tier-row tier-${goalResult.tier}`}>
                      <span className="per-goal-name">{goalResult.goal_name}</span>
                      <span className="per-goal-tier-pill">
                        {TIER_LABELS[goalResult.tier] || goalResult.tier}
                      </span>
                    </div>
                    <RuleGroups rules={goalResult.rules} />
                  </div>
                ))}
              </div>
            ) : (
              <RuleGroups rules={evaluation.results[0].rules} />
            )}
          </div>
        )}
      </section>

      <section className="about" aria-label="How it works">
        <h2>How it works</h2>
        <div className="pipeline">
          <div className="pipeline-step">
            <span className="pipeline-index">01</span>
            <h3>Real data</h3>
            <p>
              Pulls live nutrition data from USDA FoodData Central and Open
              Food Facts — no invented numbers.
            </p>
          </div>
          <div className="pipeline-step">
            <span className="pipeline-index">02</span>
            <h3>Normalized</h3>
            <p>
              Reconciles unit and schema differences between the two sources
              into one consistent format.
            </p>
          </div>
          <div className="pipeline-step">
            <span className="pipeline-index">03</span>
            <h3>Evaluated</h3>
            <p>
              A rule engine checks blocking, moderation, and bonus factors,
              sourced from FDA, AHA, and ADA guidance.
            </p>
          </div>
          <div className="pipeline-step">
            <span className="pipeline-index">04</span>
            <h3>Explained</h3>
            <p>
              Every verdict comes with the specific reasons behind it, not
              just a single number.
            </p>
          </div>
        </div>

        <h2>How a verdict is built</h2>
        <p className="section-intro">
          Every goal is made of rules, and every rule falls into one of
          three groups. The group decides how much weight it carries in
          the final verdict.
        </p>
        <div className="role-explainer">
          {ROLE_ORDER.map((role) => (
            <div key={role} className="role-card">
              <span className={`role-dot role-dot-${role}`} />
              <h3>{ROLE_INFO[role].heading}</h3>
              <p>{ROLE_INFO[role].explain}</p>
            </div>
          ))}
        </div>

        <h2>What the verdicts mean</h2>
        <p className="section-intro">
          {evaluation && SCALE_TIERS.includes(displayTier)
            ? 'The marker below shows where your last check landed.'
            : 'Check a food above to see where it lands on this scale.'}
        </p>
        <div className="tier-scale">
          {evaluation && SCALE_TIERS.includes(displayTier) && (
            <div
              className="tier-scale-marker"
              style={{
                left: `${((SCALE_TIERS.indexOf(displayTier) + 0.5) / SCALE_TIERS.length) * 100}%`,
              }}
            >
              <span className="tier-scale-arrow">▲</span>
            </div>
          )}
          {SCALE_TIERS.map((t) => (
            <div key={t} className={`tier-scale-segment tier-${t}`}>
              {TIER_LABELS[t]}
            </div>
          ))}
        </div>
        <ul className="tier-definitions">
          {SCALE_TIERS.map((t) => (
            <li key={t}>
              <strong>{TIER_LABELS[t]}</strong> — {TIER_SUMMARIES[t]}
            </li>
          ))}
          <li className="tier-definition-unknown">
            <strong>{TIER_LABELS.unknown}</strong> — not on the scale above,
            since it's not a judgment on the food. It just means there
            wasn't enough data to give a confident answer.
          </li>
        </ul>

        <h2>Why this exists</h2>
        <p className="purpose-text">
          Nutrition labels give you numbers. They don't tell you whether
          those numbers matter for your specific goal. MacroVerdict is built
          for the moment you're standing in a store aisle or looking in the
          fridge, deciding whether something actually fits what you're
          trying to do — not a full meal planner, just a straight answer on
          one item at a time.
        </p>

        <div className="medical-disclaimer-card" role="note">
          <div className="medical-disclaimer-header">
            <span className="medical-icon" aria-hidden="true">⚕️</span>
            <strong>Not Medical Advice</strong>
          </div>
          <p>
            MacroVerdict is an informational and educational tool based on public nutritional frameworks (AHA, ADA, FDA). It is <strong>not intended as clinical medical advice</strong>, diagnosis, or personalized dietary prescription. Always consult a qualified physician or registered dietitian for medical dietary decisions.
          </p>
        </div>

        <div className="community-card">
          <div className="community-card-header">
            <span className="community-badge">Community & Feedback</span>
            <h2>Help Shape MacroVerdict</h2>
          </div>
          <p className="community-description">
            Nutritional science and grocery databases are vast and complex. While our rules are modeled on established public health guidelines (AHA, ADA, FDA), <strong>the schema and threshold logic may not be perfect</strong> for every single food item or dietary need.
          </p>
          <div className="community-grid">
            <div className="community-box">
              <span className="community-icon" aria-hidden="true">💬</span>
              <div>
                <h4>Share Schema Insights</h4>
                <p>Noticed a rule evaluation or nutrient threshold that looks inaccurate? Share your feedback to help us refine the rule engine.</p>
              </div>
            </div>
            <div className="community-box">
              <span className="community-icon" aria-hidden="true">💡</span>
              <div>
                <h4>Suggest New Features</h4>
                <p>Have an idea for meal-level tracking, portion detection, new goal profiles, or scanner improvements? We’d love to hear your ideas!</p>
              </div>
            </div>
          </div>
          <div className="community-cta">
            <a
              href="https://github.com/mohammadr33/MacroVerdict/discussions"
              target="_blank"
              rel="noreferrer"
              className="community-btn-primary"
            >
              <span>Join GitHub Discussions</span>
              <span aria-hidden="true">↗</span>
            </a>
          </div>
        </div>
      </section>

      <footer className="footer">
        <p>
          Built with Python · FastAPI · React · Gemini AI ·{' '}
          <a href="https://github.com/mohammadr33/MacroVerdict" target="_blank" rel="noreferrer">Source on GitHub</a>
          {' · '}
          <a href="https://github.com/mohammadr33/MacroVerdict/discussions" target="_blank" rel="noreferrer">Discussions</a>
        </p>
        <p className="footer-demo-note">
          Not medical advice — for informational purposes only.
        </p>
        <p className="footer-demo-note">
          A solo developer personal project. Verdicts are based on public health guidelines (AHA, ADA, FDA) and schemas may not be perfect. Have ideas or insights? Feel free to share in our <a href="https://github.com/mohammadr33/MacroVerdict/discussions" target="_blank" rel="noreferrer">GitHub Discussions</a>.
        </p>
      </footer>
    </div>
  )
}

export default App