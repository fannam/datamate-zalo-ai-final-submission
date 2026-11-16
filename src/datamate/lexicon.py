"""Folded Vietnamese vocabulary for keyboard-typo repair.

Built offline from the released TRAIN questions and train rulebook only
(tokens seen at least 4 times, minus tokens that are Telex typos of a far more
frequent token). Never built from public/private test questions or answers.
Used only to repair intent keywords; entity names are matched from model frames.
"""

VOCAB = frozenset("""
a ai am an ba bac ban bang banh bao ben bi bia biet bill binh bn bnhiu bo bot bua c ca cac cach
cadivi cai cam can canh cao cap cat cau cay cetirizin chai cham chan chanh chao chat chau chay check
chenh chi chiem chien chieu chiu cho chong chu chua chuc chuyen clarityne clorid clorpheniramin co
cod coi com con cong cool cot croissant cu cua cum cung cuoi cuon d da dan dang danh dap dat dau day
dc dem den dhg dia dien dinh dip do doan doanh doi domesco don dong dot double dthu du dua dung duoc
duong e efferalgan enterogermina ep espresso file format fugacar g ga gach gan gas gaviscon ghi gi
gia giai giam gian giao gio gioi giong gium giup goi gom gop ha hai han hang hanh hao hap hapacol
hau hay hd heineken heo het hien hieu hnay ho hoa hoac hoan hoat hoi hom hon hong hop hot hqua huy
ibuprofen ich iod it j jotun json k ke kem keo ket kg khac khach khai khi kho khoai khoan khoang
khong kiem ko ky l la lai lam lan lau lavie lay le len lien liet lieu lo loai loc loi lon long
loratadin lot luat luc luon luong luot ly m ma mac mam mang mat may mg mi mieng minh ml mm moi mon
mot mua muc mui multivitamin muoi n nam namilux nang nao natri nay nep neu nganh ngay ngheu nghia
ngoai nguoi nguyen nha nhan nhap nhat nhau nhi nhieu nhiu nhom nhu nhung no noi number nuoc nuong nv
o oa ong oresol p panasonic paracetamol phai pham phan phat phe phenylephrin phi phieu phuc plusssz
pomina povidon prospan pvc qua quan quay quy ra rang rau reason revenue rieng roi rong s sa sai
salonpas san sang sau shop si simply sinh siro size so son sot sp sting strepsils sua sui suoi ta
tac tai tam tang tat te telfast ten tet th thach thai than thang thanh thay the them theo thep thi
thiet thieu thoai thoi thu thuc thung thuoc ti tien tieu tiger tinh tiramisu to toi tom ton tong top
topping total tra tram tran trang trc tren tri tro tron trong tru true trung truoc truong tu tuan
tuc tung tuoi tuong tuyp ty type ung uong va vai van vang vao vay ve vi vien viet vitamin voi von vs
vu weber wide x xanh xao xay xe xem xep xet xi xoai xuat y z zalo
""".split())
