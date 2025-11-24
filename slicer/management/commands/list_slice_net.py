import json
import subprocess
from django.core.management.base import BaseCommand
from django.conf import settings

class Command(BaseCommand):
    help = (
        "List network slices with Docker network and QoS (tc) state. "
        "Shows macvlan (mvlanX) and optional IFB ingress devices if bidirectional QoS is enabled. "
        "Use --json for machine-readable output."
    )

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Output JSON instead of text table')
        parser.add_argument('--all', action='store_true', help='Include inactive slices')
        parser.add_argument('--slice-id', type=str, help='Filter by specific slice UUID')

    def handle(self, *args, **options):
        from slicer.models import NetworkSlice
        from slicer.docker_manager import DockerVLANManager

        qs = NetworkSlice.objects.all().order_by('-created_at')
        if not options.get('all'):
            qs = qs.exclude(status='INACTIVE')
        if options.get('slice_id'):
            qs = qs.filter(id=options['slice_id'])

        docker_mgr = DockerVLANManager()
        data = []
        for sl in qs:
            info = docker_mgr.get_slice_network_info(sl) or {}
            vlan_id = getattr(sl, 'vlan_id', None)
            iface = f"mvlan{vlan_id}" if vlan_id else None
            ifb_iface = f"ifb{vlan_id}" if vlan_id else None
            qdisc = self._safe_tc_show(iface) if iface else None
            ifb_qdisc = self._safe_tc_show(ifb_iface) if iface and ifb_iface else None
            data.append({
                'id': str(sl.id),
                'name': sl.name,
                'type': sl.slice_type,
                'status': sl.status,
                'bandwidth_mbps': sl.bandwidth_mbps,
                'latency_ms': sl.latency_ms,
                'vlan_id': vlan_id,
                'docker_network': info.get('network_name'),
                'discoverable': info.get('discoverable'),
                'discovery_url': info.get('discovery_url'),
                'host_interface': iface,
                'host_qdisc': qdisc,
                'ingress_ifb': ifb_iface if getattr(settings, 'ENABLE_BIDIRECTIONAL_QOS', False) else None,
                'ingress_qdisc': ifb_qdisc if getattr(settings, 'ENABLE_BIDIRECTIONAL_QOS', False) else None,
            })

        if options.get('json'):
            self.stdout.write(json.dumps({'slices': data}, indent=2))
            return

        if not data:
            self.stdout.write('No slices match criteria.')
            return

        # Text output
        for entry in data:
            self.stdout.write(f"Slice {entry['name']} ({entry['id']}) [{entry['status']}] type={entry['type']}")
            self.stdout.write(f"  VLAN: {entry['vlan_id']}  DockerNet: {entry['docker_network']}  Discoverable: {entry['discoverable']}")
            if entry['discovery_url']:
                self.stdout.write(f"  Discovery URL: {entry['discovery_url']}")
            self.stdout.write(f"  QoS: {entry['bandwidth_mbps']}Mbps / {entry['latency_ms']}ms  Interface: {entry['host_interface']}")
            if entry['host_qdisc']:
                self.stdout.write("  Host QDisc:")
                for line in entry['host_qdisc'].splitlines():
                    self.stdout.write(f"    {line}")
            if entry.get('ingress_ifb') and entry.get('ingress_qdisc'):
                self.stdout.write(f"  Ingress IFB: {entry['ingress_ifb']}")
                for line in entry['ingress_qdisc'].splitlines():
                    self.stdout.write(f"    {line}")
            self.stdout.write("")

    def _safe_tc_show(self, iface):
        if not iface:
            return None
        try:
            result = subprocess.run(['tc', 'qdisc', 'show', 'dev', iface], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception:
            return None