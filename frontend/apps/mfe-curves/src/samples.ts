// Illustrative synthetic quotes (not market data) to try the calculator.
export const SAMPLE_BID_ASK = [
  { tenor: '1W', bid: 20.9, ask: 21.1 },
  { tenor: '1M', bid: 20.7, ask: 20.95 },
  { tenor: '2M', bid: 20.4, ask: 20.7 },
  { tenor: '3M', bid: 20.05, ask: 20.4 },
  { tenor: '6M', bid: 19.2, ask: 19.6 },
  { tenor: '9M', bid: 18.45, ask: 18.85 },
  { tenor: '1Y', bid: 17.7, ask: 18.1 },
  { tenor: '18M', bid: 16.55, ask: 17.05 },
  { tenor: '2Y', bid: 15.95, ask: 16.45 },
  { tenor: '3Y', bid: 15.0, ask: 15.6 },
  { tenor: '4Y', bid: 14.7, ask: 15.3 },
  { tenor: '5Y', bid: 14.25, ask: 14.85 },
  { tenor: '7Y', bid: 14.05, ask: 14.75 },
  { tenor: '10Y', bid: 13.8, ask: 14.6 },
]

export const SAMPLE_MID_SPREAD = SAMPLE_BID_ASK.map((q) => ({
  tenor: q.tenor,
  mid: Math.round(((q.bid + q.ask) / 2) * 1000) / 1000,
  spread: Math.round((q.ask - q.bid) * 1000) / 1000,
}))
