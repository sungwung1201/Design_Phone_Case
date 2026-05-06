const sendOrder = async () => {
  const imageBase64 = canvas.toDataURL("image/png");

  const res = await fetch("http://localhost:5000/api/orders", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model: "iphone14",
      caseType: "hard",
      caseColor: "black",
      totalPrice: 12000,
      imageBase64: imageBase64
    })
  });

  const data = await res.json();
  console.log(data);
};