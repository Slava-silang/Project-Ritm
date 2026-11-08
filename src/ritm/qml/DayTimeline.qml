pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

Rectangle {
    id: timeline
    required property var backend
    required property var view
    signal editBlock(var entry)
    property real minuteHeight: 1.2
    implicitHeight: 64 + (view.timelineMinutes || 1440) * minuteHeight + 20
    radius: 14
    color: "#ffffff"
    Label {
        x: 14
        y: 12
        text: qsTr("Шкала дня · ") + (timeline.view.selectedLabel || "")
        color: "#243d33"
        font.pixelSize: 18
        font.bold: true
    }
    Item {
        id: chart
        x: 8
        y: 58
        width: parent.width - 16
        height: (timeline.view.timelineMinutes || 1440) * timeline.minuteHeight
        Repeater {
            model: timeline.view.ticks || []
            delegate: Item {
                id: tick
                required property var modelData
                y: modelData.minute * timeline.minuteHeight
                width: chart.width
                Label {
                    width: 80
                    y: -8
                    text: tick.modelData.label
                    color: "#69766d"
                    font.pixelSize: 11
                }
                Rectangle {
                    x: 84
                    width: Math.max(0, parent.width - 84)
                    height: 1
                    color: "#e5eadd"
                }
            }
        }
        Repeater {
            model: timeline.view.timeline || []
            delegate: Rectangle {
                id: blockItem
                required property var modelData
                property int previewDelta: 0
                property string gesture: "move"
                property bool pending: false
                x: 86 + (chart.width - 88) * modelData.lane / timeline.view.timelineLanes
                y: modelData.offsetMinutes * timeline.minuteHeight + (gesture === "move" ? previewDelta * timeline.minuteHeight : 0)
                width: (chart.width - 88) / timeline.view.timelineLanes - 4
                height: Math.max(44, modelData.segmentMinutes * timeline.minuteHeight + (gesture === "resize" ? previewDelta * timeline.minuteHeight : 0))
                z: previewDelta !== 0 ? 3 : 1
                radius: 8
                color: previewDelta !== 0 ? "#d2e4c8" : modelData.isEvent ? "#eee6f5" : "#e6efdf"
                border.color: modelData.warning ? "#b96d40" : "#6e9578"
                border.width: pending || previewDelta !== 0 ? 2 : 1
                Connections {
                    target: timeline.backend
                    function onStateChanged() {
                        if (!timeline.backend.busy) {
                            blockItem.previewDelta = 0;
                            blockItem.pending = false;
                        }
                    }
                }
                function submit(kind) {
                    if (previewDelta === 0)
                        return;
                    pending = true;
                    timeline.backend.changeBlock({
                        id: modelData.id,
                        expectedStart: modelData.startsAt,
                        expectedEnd: modelData.endsAt,
                        expectedTitle: modelData.eventTitle,
                        operation: kind,
                        minutes: previewDelta
                    });
                }
                Column {
                    x: 7
                    y: 4
                    width: parent.width - 36
                    Label {
                        width: parent.width
                        text: blockItem.modelData.time
                        font.pixelSize: 11
                        font.bold: true
                        color: "#2b5949"
                        elide: Text.ElideRight
                    }
                    Label {
                        width: parent.width
                        textFormat: Text.PlainText
                        text: blockItem.modelData.title
                        font.pixelSize: 12
                        color: "#243d33"
                        elide: Text.ElideRight
                    }
                    Label {
                        width: parent.width
                        visible: !!blockItem.modelData.warning && blockItem.height >= 60
                        text: blockItem.modelData.warning
                        elide: Text.ElideRight
                        color: "#9a4b24"
                        font.pixelSize: 11
                    }
                    Label {
                        visible: blockItem.previewDelta !== 0
                        text: (blockItem.previewDelta > 0 ? "+" : "") + blockItem.previewDelta + qsTr(" мин")
                        color: "#2b5949"
                        font.pixelSize: 11
                    }
                }
                MouseArea {
                    id: mover
                    objectName: "timelineMove_" + blockItem.modelData.id
                    anchors.fill: parent
                    anchors.bottomMargin: 10
                    preventStealing: true
                    enabled: !timeline.backend.busy && blockItem.modelData.canMove
                    hoverEnabled: true
                    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                    property real pressY: 0
                    property bool cancelled: false
                    onPressed: mouse => {
                        forceActiveFocus();
                        cancelled = false;
                        blockItem.gesture = "move";
                        pressY = mapToItem(chart, mouse.x, mouse.y).y;
                    }
                    onPositionChanged: mouse => {
                        if (pressed && !cancelled)
                            blockItem.previewDelta = Math.round((mapToItem(chart, mouse.x, mouse.y).y - pressY) / (15 * timeline.minuteHeight)) * 15;
                    }
                    onReleased: {
                        if (!cancelled)
                            blockItem.submit("move");
                    }
                    onCanceled: if (!blockItem.pending)
                        blockItem.previewDelta = 0
                    onDoubleClicked: timeline.editBlock(blockItem.modelData)
                    Keys.onEscapePressed: {
                        cancelled = true;
                        blockItem.previewDelta = 0;
                    }
                }
                ToolTip.visible: mover.containsMouse && !mover.pressed && !!modelData.warning
                ToolTip.text: modelData.warning
                ToolButton {
                    objectName: "timelineEdit_" + blockItem.modelData.id
                    anchors.right: parent.right
                    anchors.top: parent.top
                    width: 27
                    height: 28
                    text: "⋯"
                    enabled: !timeline.backend.busy
                    Accessible.name: qsTr("Изменить рабочий блок")
                    onClicked: timeline.editBlock(blockItem.modelData)
                }
                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 9
                    radius: 4
                    color: blockItem.modelData.canResize ? (blockItem.modelData.isEvent ? "#9784ad" : "#7c9c79") : "#c4d3bc"
                    MouseArea {
                        objectName: "timelineResize_" + blockItem.modelData.id
                        anchors.fill: parent
                        preventStealing: true
                        enabled: !timeline.backend.busy && blockItem.modelData.canResize
                        cursorShape: Qt.SizeVerCursor
                        property real pressY: 0
                        property bool cancelled: false
                        onPressed: mouse => {
                            forceActiveFocus();
                            cancelled = false;
                            blockItem.gesture = "resize";
                            pressY = mapToItem(chart, mouse.x, mouse.y).y;
                        }
                        onPositionChanged: mouse => {
                            if (pressed && !cancelled)
                                blockItem.previewDelta = Math.round((mapToItem(chart, mouse.x, mouse.y).y - pressY) / (15 * timeline.minuteHeight)) * 15;
                        }
                        onReleased: {
                            if (!cancelled)
                                blockItem.submit("resize");
                        }
                        onCanceled: if (!blockItem.pending)
                            blockItem.previewDelta = 0
                        Keys.onEscapePressed: {
                            cancelled = true;
                            blockItem.previewDelta = 0;
                        }
                    }
                }
            }
        }
    }
}
