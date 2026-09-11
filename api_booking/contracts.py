"""개인정보/인증 원문을 저장하지 않는 요청 구조와 실행 내 토큰 연결 관측."""
import hashlib
import hmac
import secrets

PATHS = {
    '/api/ap/booking/avail/calendarFareMatrix', '/api/ap/booking/avail/awardAvailability',
    '/api/ap/booking/avail/fareInformation', '/api/ap/booking/traveller/inputTravellers',
    '/api/li/member/validateMember', '/api/pp/payment/GetAvailablePaymentType',
    '/api/pp/payment/NaverPay',
    '/api/ss/skypass/member/searchFamilyInfoList', '/api/ss/skypass/member/searchMemberSubscriptions',
    '/api/li/member/searchMemberCreditCard', '/api/et/ibeSupport/officeMeta',
    '/api/et/route/c/a/isAwardRoute', '/api/et/ibeSupport/c/e/bookingToCabin',
    '/api/et/route/c/a/getReservationAirport', '/api/et/uiCommon/discountPtc',
    '/api/et/ibeSupport/c/e/allowUmnrAge', '/api/et/ibeSupport/baggagePolicies',
    '/api/et/ibeSupport/FareRuleWithAdmin', '/api/pp/payment/GetPaymentAlert',
    '/api/pp/payment/GiftCardList', '/api/pp/payment/PlccSearch',
    # 5064/8273 공개 호출부에서 확인. 실제 발생 시에만 관측하며 전송하지 않는다.
    '/api/ss/skypass/redemption/optimizedFamilyPlan',
    '/api/et/bonusDeduct/bonusBookingDeductMileage',
}
TOKEN_FIELDS = {'pageTicket','cartId','orderId','reservationId','bookingReference','pnr','recordLocator',
                'offerId','fareId','flightId','boundId','paymentId','transactionId','payId',
                'recommendId','reserveId','orderNo','reservationRecLoc'}
FIELDS = TOKEN_FIELDS | set('''data result body error errors code errorCode message status success
    order reservation booking payment redirectUrl redirectURL url nextUrl value items
    segmentList departureDate arrivalDate departureAirport arrivalAirport travelers travellers
    travelerList travellerList passengerList passengerType passengerTypeCode ptc count
    boundFareCalendarList fareCalendarList fareFamilyList fareFamilyStatus emptyFare
    upsellBoundAvailList availFlightList flightInfoList commercialFareFamilyList fareFamily
    seatCount soldout flightNumber operationCarrierCode marketingCarrierCode codeShare
    departureTime arrivalTime cabin cabinClass currency currencyCode selectedCurrency
    flightList boundList selectedFlightList fareList fareInfo fareInformation fareBasis
    totalAmount taxAmount totalMileage amount mileage fare conditions inputVo availCriteria
    availablePaymentTypeList paymentType paymentTypeList paymentMethod paymentMethodList
    language lang locale countryCode isSuccess errorMessage errorList validationResult
    commonRequest commonResponse selectedBoundList selectedFare selectedFlight
    travellerInfo travelerInfo contactInfo contact passenger passengerInfo passengers
    fareCalculation fareComponentList itinerary originDestinationList flightSelection
    selectedCommercialFareFamily recommendationList recommendationId
    reservationInfo orderInfo paymentInfo response responseCode responseMessage
    fareSelection flightSelectionList flightIndex boundIndex itineraryList
    cart passengerId travellerId travelerId paymentOption paymentOptionList
    origin destination tripType region channel tripFlow selectedFareFamily
    recommendList travellerInfoList contactList preferLanguage mode resultCode
    paymentAmount officeId deviceCode callbackUrl callbackURL
    pnrFareInfo travellerFareInfoList taxes fees totalTax segmentStatus statusCode
    departureDateTime arrivalDateTime bookingClass
'''.split())

# 공개 호출부에서 비교를 확인한 값만 보존한다. HK의 마일리지 보유 의미는 미검증.
OBSERVED_ENUMS = {'segmentStatus': {'HK'}, 'statusCode': {'HK'},
                 'resultCode': {'Success'}, 'currency': {'KRW', 'USD'}}
OBSERVED_BOOLEANS = {'soldout', 'emptyFare', 'success', 'isSuccess'}


class ContractRecorder:
    def __init__(self):
        self._key = secrets.token_bytes(32)
        self._refs = {}
        self.rows = []
        self._sources = {}
        self.links = []

    def project(self, data):
        remaining = [5000]
        def visit(value, field='', depth=0, path=()):
            remaining[0] -= 1
            if depth > 10 or remaining[0] < 0:
                return {'type':'truncated'}
            if isinstance(value, dict):
                allowed={k:visit(v,k,depth+1,path+(k,)) for k,v in value.items() if k in FIELDS}
                return {'type':'object','fields':allowed,'omittedFieldCount':sum(k not in FIELDS for k in value)}
            if isinstance(value, list):
                return {'type':'array','count':len(value),'items':[visit(v,field,depth+1,path+('*',)) for v in value[:100]],
                        'truncated':len(value)>100}
            kind = 'null' if value is None else 'boolean' if isinstance(value,bool) else 'number' if isinstance(value,(int,float)) else 'string'
            result = {'type':kind}
            # 113950 정상 UI 관측의 정확한 경로만 허용. 일반 status 문자열은 비저장.
            if path == ('boundList','*','segmentList','*','status') and isinstance(value,str):
                if value == 'HK':
                    result['observedValue'] = value
                else:
                    result['unrecognizedValue'] = True
            if field in OBSERVED_ENUMS and isinstance(value, str):
                if value in OBSERVED_ENUMS[field]:
                    result['observedValue'] = value
                else:
                    result['unrecognizedValue'] = True
            if field in OBSERVED_BOOLEANS and type(value) is bool:
                result['observedValue'] = value
            if field in TOKEN_FIELDS and isinstance(value,str) and value and len(self._refs)<10000:
                digest=hmac.new(self._key,value.encode(),hashlib.sha256).digest()
                result['ref']=self._refs.setdefault(digest,'v'+str(len(self._refs)+1))
            return result
        return visit(data)

    @staticmethod
    def references(projected, path='$'):
        if 'ref' in projected:
            yield path, projected['ref']
        for key, value in projected.get('fields', {}).items():
            yield from ContractRecorder.references(value, path+'.'+key)
        for index,value in enumerate(projected.get('items',[])):
            yield from ContractRecorder.references(value,path+f'[{index}]')

    def record(self, path, phase, at, data, request_id, **metadata):
        if path not in PATHS or phase not in ('request','response'):
            return
        projected=self.project(data)
        row={'id':len(self.rows)+1,'requestId':request_id,'path':path,'phase':phase,'at':at,'structure':projected}
        row.update({k:v for k,v in metadata.items() if k in ('httpStatus','networkMs','method')})
        self.rows.append(row)
        for field,ref in self.references(projected):
            if phase=='request':
                for source in self._sources.get(ref,[]):
                    if source['path'] != path and source['at'] <= at:
                        self.links.append({'fromRow':source['row'],'fromField':source['field'],
                                           'toRow':row['id'],'toField':field,'ref':ref})
            else:
                self._sources.setdefault(ref,[]).append({'row':row['id'],'field':field,'at':at,'path':path})

    def snapshot(self):
        return {'rows':self.rows,'observedValueLinks':self.links,
                'interpretation':'같은 실행의 앞선 응답과 뒤 요청에 같은 값이 나타난 관측. 필수 의존관계나 생략 가능성의 증명은 아님.',
                'privacy':'원문 본문·인증·개인정보·HMAC 키는 저장하지 않음. 허용 구조·실행 내 임의 참조·제한된 관측 코드/불리언만 저장.',
                'valuePolicy': {'enums':{k:sorted(v) for k,v in OBSERVED_ENUMS.items()},
                                'pathEnums':{'$.boundList[*].segmentList[*].status':['HK']},
                                'booleans':sorted(OBSERVED_BOOLEANS)},
                'seatHoldVerified':False}
