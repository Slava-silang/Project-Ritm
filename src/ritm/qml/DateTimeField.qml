pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

TextField {
    id: field
    required property var appearance
    property bool dateOnly: false
    property bool timeOnly: false
    property bool isoDate: false
    property bool allowEmpty: false
    property bool allowOffset: false
    property bool allow24: false
    rightPadding: chooser.width + appearance.dp(8)
    Layout.minimumWidth: 0

    function pad(value, count = 2) { return String(value).padStart(count, "0"); }
    function day(year, month, number) {
        const value = new Date(2000, 0, 1, 12);
        value.setFullYear(year, month, number);
        return value;
    }
    function iso(value) {
        return pad(value.getFullYear(), 4) + "-" + pad(value.getMonth() + 1) + "-" + pad(value.getDate());
    }
    function openPicker() {
        const now = new Date();
        let selected = now;
        let hour = now.getHours(), minute = now.getMinutes(), offset = "";
        const value = text.trim();
        const match = field.timeOnly ? /^(\d{1,2}):(\d{2})$/.exec(value)
            : field.isoDate ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
            : /^(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?(?:\s+([+-]\d{2}:\d{2}))?$/.exec(value);
        if (match) {
            if (field.timeOnly) {
                hour = Number(match[1]); minute = Number(match[2]);
            } else if (field.isoDate) {
                selected = day(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
            } else {
                selected = day(Number(match[3]), Number(match[2]) - 1, Number(match[1]));
                if (match[4] !== undefined) {
                    hour = Number(match[4]); minute = Number(match[5]);
                }
                offset = match[6] || "";
            }
        }
        picker.chosen = selected;
        picker.viewYear = selected.getFullYear();
        picker.viewMonth = selected.getMonth();
        hours.value = Math.min(field.allow24 ? 24 : 23, hour);
        minutes.value = minute;
        offsetInput.text = offset;
        picker.open();
    }
    ToolButton {
        id: chooser
        objectName: field.objectName + "Picker"
        anchors.right: parent.right
        anchors.top: parent.top
        height: parent.height
        width: field.appearance.dp(36)
        text: field.timeOnly ? "◷" : "▦"
        Accessible.name: field.timeOnly ? qsTr("Выбрать время") : qsTr("Выбрать дату и время")
        onClicked: field.openPicker()
    }
    Dialog {
        id: picker
        objectName: field.objectName + "DateTimePicker"
        property date chosen: new Date()
        property int viewYear: new Date().getFullYear()
        property int viewMonth: new Date().getMonth()
        property var days: {
            const first = field.day(viewYear, viewMonth, 1);
            const offset = (first.getDay() + 6) % 7;
            const count = Math.ceil((offset + field.day(viewYear, viewMonth + 1, 0).getDate()) / 7) * 7;
            let result = [];
            for (let i = 0; i < count; ++i)
                result.push(field.day(viewYear, viewMonth, 1 - offset + i));
            return result;
        }
        function changeMonth(delta) {
            const value = field.day(viewYear, viewMonth + delta, 1);
            if (value.getFullYear() >= 1 && value.getFullYear() <= 9999) {
                viewYear = value.getFullYear();
                viewMonth = value.getMonth();
            }
        }
        function commit(value) {
            field.text = value;
            field.textEdited();
            close();
        }
        title: field.timeOnly ? qsTr("Выбрать время") : field.dateOnly ? qsTr("Выбрать дату") : qsTr("Выбрать дату и время")
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(field.appearance.dp(480), parent ? parent.width - 24 : 480)
        height: Math.min(field.appearance.dp(field.timeOnly ? 320 : 560), parent ? parent.height - 24 : 560)
        modal: true
        background: Rectangle {
            color: field.appearance.colors.surface
            border.color: field.appearance.colors.border
            radius: field.appearance.dp(12)
        }
        contentItem: ScrollView {
            id: scroll
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: scroll.availableWidth
                spacing: field.appearance.dp(8)
                RowLayout {
                    Layout.fillWidth: true
                    visible: !field.timeOnly
                    ToolButton {
                        objectName: field.objectName + "PreviousMonth"
                        text: "←"
                        Accessible.name: qsTr("Предыдущий месяц")
                        onClicked: picker.changeMonth(-1)
                    }
                    ComboBox {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        model: [qsTr("Январь"), qsTr("Февраль"), qsTr("Март"), qsTr("Апрель"), qsTr("Май"), qsTr("Июнь"), qsTr("Июль"), qsTr("Август"), qsTr("Сентябрь"), qsTr("Октябрь"), qsTr("Ноябрь"), qsTr("Декабрь")]
                        currentIndex: picker.viewMonth
                        onActivated: picker.viewMonth = currentIndex
                    }
                    SpinBox {
                        objectName: field.objectName + "Year"
                        Layout.preferredWidth: field.appearance.dp(140)
                        from: 1
                        to: 9999
                        editable: true
                        wheelEnabled: true
                        value: picker.viewYear
                        textFromValue: value => String(value)
                        valueFromText: value => parseInt(value, 10)
                        onValueModified: picker.viewYear = value
                    }
                    ToolButton {
                        objectName: field.objectName + "NextMonth"
                        text: "→"
                        Accessible.name: qsTr("Следующий месяц")
                        onClicked: picker.changeMonth(1)
                    }
                }
                GridLayout {
                    id: grid
                    Layout.fillWidth: true
                    columns: 7
                    columnSpacing: field.appearance.dp(2)
                    rowSpacing: field.appearance.dp(2)
                    visible: !field.timeOnly
                    Repeater {
                        model: [qsTr("Пн"), qsTr("Вт"), qsTr("Ср"), qsTr("Чт"), qsTr("Пт"), qsTr("Сб"), qsTr("Вс")]
                        Label {
                            required property string modelData
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            text: modelData
                        }
                    }
                    Repeater {
                        model: picker.visible && !field.timeOnly ? picker.days : []
                        Button {
                            required property date modelData
                            objectName: field.objectName + "Day_" + field.iso(modelData)
                            Layout.preferredWidth: (grid.width - 6 * grid.columnSpacing) / 7
                            Layout.minimumWidth: 0
                            Layout.preferredHeight: field.appearance.dp(32)
                            padding: 0
                            text: modelData.getDate()
                            opacity: modelData.getMonth() === picker.viewMonth ? 1 : 0.55
                            highlighted: field.iso(modelData) === field.iso(picker.chosen)
                            enabled: modelData.getFullYear() >= 1 && modelData.getFullYear() <= 9999
                            onClicked: {
                                picker.chosen = modelData;
                                picker.viewYear = modelData.getFullYear();
                                picker.viewMonth = modelData.getMonth();
                            }
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: !field.dateOnly
                    wrapMode: Text.Wrap
                    text: qsTr("Часы и минуты: стрелки или колесо мыши")
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: !field.dateOnly
                    SpinBox {
                        id: hours
                        objectName: field.objectName + "Hour"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        from: 0
                        to: field.allow24 ? 24 : 23
                        editable: true
                        wheelEnabled: true
                        wrap: true
                        textFromValue: value => field.pad(value)
                        valueFromText: value => parseInt(value, 10)
                    }
                    Label { text: ":" }
                    SpinBox {
                        id: minutes
                        objectName: field.objectName + "Minute"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        from: 0
                        to: hours.value === 24 ? 0 : 59
                        editable: true
                        wheelEnabled: true
                        wrap: true
                        textFromValue: value => field.pad(value)
                        valueFromText: value => parseInt(value, 10)
                    }
                }
                TextField {
                    id: offsetInput
                    objectName: field.objectName + "Offset"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: field.allowOffset && !field.timeOnly && !field.dateOnly
                    placeholderText: qsTr("Часовой пояс необязательно: +03:00")
                }
            }
        }
        footer: Flow {
            width: picker.availableWidth
            padding: field.appearance.dp(8)
            spacing: field.appearance.dp(6)
            Button {
                objectName: field.objectName + "PickerApply"
                text: qsTr("Выбрать")
                onClicked: {
                    let value = field.isoDate ? field.iso(picker.chosen) : field.pad(picker.chosen.getDate()) + "." + field.pad(picker.chosen.getMonth() + 1) + "." + field.pad(picker.chosen.getFullYear(), 4);
                    const clock = field.pad(hours.value) + ":" + field.pad(minutes.value);
                    if (field.timeOnly) value = clock;
                    else if (!field.dateOnly) value += " " + clock + (field.allowOffset && offsetInput.text.trim() ? " " + offsetInput.text.trim() : "");
                    picker.commit(value);
                }
            }
            Button {
                objectName: field.objectName + "PickerNow"
                text: field.timeOnly ? qsTr("Текущее время") : qsTr("Сегодня")
                onClicked: {
                    const now = new Date();
                    picker.chosen = now;
                    picker.viewYear = now.getFullYear();
                    picker.viewMonth = now.getMonth();
                    if (field.timeOnly) { hours.value = now.getHours(); minutes.value = now.getMinutes(); }
                }
            }
            Button {
                objectName: field.objectName + "PickerClear"
                visible: field.allowEmpty
                text: qsTr("Очистить")
                onClicked: picker.commit("")
            }
            Button {
                objectName: field.objectName + "PickerCancel"
                text: qsTr("Отмена")
                onClicked: picker.close()
            }
        }
    }
}
