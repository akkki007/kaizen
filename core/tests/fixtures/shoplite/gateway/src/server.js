const express = require("express");
const orders = require("../../orders/src/orders");

const app = express();

app.post("/orders", async (req, res) => {
  const ok = orders.createOrder(req.body.items);
  res.json({ ok });
});

module.exports = app;
