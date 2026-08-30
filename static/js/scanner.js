function onScanSuccess(decodedText) {
    html5QrcodeScanner.clear();
    document.getElementById('result').classList.remove('d-none');

    fetch('/scan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({student_id: decodedText})
    })
   .then(res => res.json())
   .then(data => {
        if(data.status == 'success'){
            document.getElementById('student-info').innerHTML = `
                <img src="https://ui-avatars.com/api/?name=${data.name}" class="rounded-circle mb-2">
                <h5>${data.name}</h5>
                <p>${data.section}</p>
                <p><b>Time:</b> ${data.time}</p>
                <p><b>Status:</b> ${data.message}</p>
                <button class="btn btn-primary w-100">Save Attendance</button>
            `;
        } else {
            alert(data.message);
        }
    });
}

let html5QrcodeScanner = new Html5QrcodeScanner("reader", { fps: 10, qrbox: 250 });
html5QrcodeScanner.render(onScanSuccess);