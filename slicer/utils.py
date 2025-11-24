# slicer/utils.py
import qrcode
import io
import base64
from django.core.files.base import ContentFile

def generate_wifi_qr_code(ssid, password, security='WPA'):
    """Generate QR code for WiFi connection"""
    # WiFi format: WIFI:S:<SSID>;T:<Security>;P:<Password>;;
    wifi_config = f"WIFI:S:{ssid};T:{security};P:{password};;"
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(wifi_config)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to base64 for web display
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    img_str = base64.b64encode(buffer.getvalue()).decode()
    
    return f"data:image/png;base64,{img_str}"

# In your views.py
@action(detail=True, methods=['get'])
def qr_code(self, request, pk=None):
    """Get QR code for slice WiFi connection"""
    slice_obj = self.get_object()
    
    if slice_obj.status != 'ACTIVE':
        return Response({'error': 'Slice is not active'}, status=400)
    
    qr_code_data = generate_wifi_qr_code(
        slice_obj.ssid_name, 
        slice_obj.wifi_password
    )
    
    return Response({
        'ssid': slice_obj.ssid_name,
        'password': slice_obj.wifi_password,
        'qr_code': qr_code_data
    })