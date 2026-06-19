// Trade data placeholder.
//
// This file intentionally ships EMPTY — no mock/sample trades. The dashboard
// shows ONLY the agent's own trades, pulled live from the bot at
// /api/agent/trades. Account balance/equity below are cold-start values —
// they are OVERWRITTEN by the live ledger as soon as the bot connects.
window.tradeData = {
  account: {
    login: 0,
    currency: "INR",
    balance: 100000,
    equity: 100000,
    broker: "Paper",
  },
  generatedAt: new Date().toISOString(),
  isMockData: false,
  openPositions: [],
  trades: [],
  closedTrades: [],
};
