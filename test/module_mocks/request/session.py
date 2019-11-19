
class MockGetResponse(object):
    def __init__(self, text):
        self.text = text

class MockSession(object):
    def get(self, url):
        if url.find('login') < 0:
            return MockGetResponse("""
                <ajax_response_xml_root>
                    <IF_ERRORPARAM>SUCC</IF_ERRORPARAM>
                    <IF_ERRORTYPE>SUCC</IF_ERRORTYPE>
                    <IF_ERRORSTR>SUCC</IF_ERRORSTR>
                    <IF_ERRORID>0</IF_ERRORID>
                    <OBJ_ACCESSDEV_ID>
                        <Instance>
                            <ParaName>_InstID</ParaName>
                            <ParaValue>DEV.Hosts.HI337</ParaValue>
                            <ParaName>AliasName</ParaName>
                            <ParaValue></ParaValue>
                            <ParaName>IPAddress</ParaName>
                            <ParaValue>192.168.2.16</ParaValue>
                            <ParaName>HostName</ParaName>
                            <ParaValue>Samsung</ParaValue>
                            <ParaName>MACAddress</ParaName>
                            <ParaValue>40:5a:a4:6b:33:4b</ParaValue>
                            <ParaName>IPV6Address</ParaName>
                            <ParaValue>::;::;::;::;::</ParaValue>
                            <ParaName>InterfaceType</ParaName>
                            <ParaValue>802.11</ParaValue>
                            <ParaName>AddressSource</ParaName>
                            <ParaValue>DHCP</ParaValue>
                        </Instance>
                    </OBJ_ACCESSDEV_ID>
                </ajax_response_xml_root>
            """)
        return MockGetResponse('<a login token 1234>;')

    def post(self, url, data):
        return ""
