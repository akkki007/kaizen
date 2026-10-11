const express = require("express");
const { validatePayment, formatReceipt } = require("./payment/validation");

const app = express();

function handlePayment(req, res) {
  if (!validatePayment(req.body)) {
    return res.status(400).end();
  }
  res.json({ receipt: formatReceipt(req.body) });
}

app.post("/payments", handlePayment);
app.get("/health", (req, res) => res.json({ ok: true }));

module.exports = app;
