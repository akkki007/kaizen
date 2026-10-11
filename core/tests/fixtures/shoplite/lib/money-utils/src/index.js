function toCents(amount) {
  return Math.round(amount * 100);
}

function formatMoney(cents, currency) {
  return `${(cents / 100).toFixed(2)} ${currency}`;
}

module.exports = { toCents, formatMoney };
