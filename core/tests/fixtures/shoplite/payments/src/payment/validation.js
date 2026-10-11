const { toCents } = require("money-utils");

function validatePayment(payment) {
  if (!payment.currency) {
    throw new Error("currency required");
  }
  return toCents(payment.amount) > 0;
}

function formatReceipt(payment) {
  return `receipt:${payment.id}`;
}

module.exports = { validatePayment, formatReceipt };
