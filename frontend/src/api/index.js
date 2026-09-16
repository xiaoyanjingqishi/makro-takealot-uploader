const BASE_URL = "http://localhost:8000/api";

export async function fetchProducts(params = {}) {
  const query = new URLSearchParams(params).toString();
  const res = await fetch(`${BASE_URL}/products?${query}`);
  return res.json();
}

export async function getProduct(id) {
  const res = await fetch(`${BASE_URL}/products/${id}`);
  return res.json();
}

export async function updateProduct(id, data) {
  const res = await fetch(`${BASE_URL}/products/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });
  return res.json();
}

export async function cleanProduct(id) {
  const res = await fetch(`${BASE_URL}/cleaner/clean/${id}`, { method: "POST" });
  return res.json();
}

export async function batchClean(productIds) {
  const res = await fetch(`${BASE_URL}/cleaner/batch-clean`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ product_ids: productIds })
  });
  return res.json();
}

export async function publishProduct(id) {
  const res = await fetch(`${BASE_URL}/makro/publish/${id}`, { method: "POST" });
  return res.json();
}

export async function fetchSettings() {
  const res = await fetch(`${BASE_URL}/settings`);
  return res.json();
}

export async function saveSettings(data) {
  const res = await fetch(`${BASE_URL}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });
  return res.json();
}

export async function fetchTasks(limit = 50) {
  const res = await fetch(`${BASE_URL}/tasks?limit=${limit}`);
  return res.json();
}
