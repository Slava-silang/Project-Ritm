pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

Rectangle {
    id: timeline
    required property var backend
    required property var appearance
    required property var view
    signal editBlock(var entry)
    property real minuteHeight: timeline.appearance.dp(1.2)
    implicitHeight: timeline.appearance.dp(84) + (view.timelineMinutes || 1440) * minuteHeight
    radius: timeline.appearance.dp(14)
    color: timeline.appearance.colors.surface
    Label {
        x: timeline.appearance.dp(14)
        y: timeline.appearance.dp(12)
        text: qsTr("Шкала дня · ") + (timeline.view.selectedLabel || "")
        color: timeline.appearance.colors.text
        font.pixelSize: timeline.appearance.dp(18)
        font.bold: true
    }
    Item {
        id: chart
        x: timeline.appearance.dp(8)
        y: timeline.appearance.dp(58)
        width: parent.width - timeline.appearance.dp(16)
        height: (timeline.view.timelineMinutes || 1440) * timeline.minuteHeight
        Repeater {
            model: timeline.view.ticks || []
            delegate: Item {
                id: tick
                required property var modelData
                y: modelData.minute * timeline.minuteHeight
                width: chart.width
                Label {
                    width: timeline.appearance.dp(80)
                    y: -timeline.appearance.dp(8)
                    text: tick.modelData.label
                    color: timeline.appearance.colors.muted
                    font.pixelSize: timeline.appearance.dp(11)
                }
                Rectangle {
                    x: timeline.appearance.dp(84)
                    width: Math.max(0, parent.width - timeline.appearance.dp(84))
                    height: timeline.appearance.dp(1)
                    color: timeline.appearance.colors.track
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
                x: timeline.appearance.dp(86) + (chart.width - timeline.appearance.dp(88)) * modelData.lane / timeline.view.timelineLanes
                y: modelData.offsetMinutes * timeline.minuteHeight + (gesture === "move" ? previewDelta * timeline.minuteHeight : 0)
                width: (chart.width - timeline.appearance.dp(88)) / timeline.view.timelineLanes - timeline.appearance.dp(4)
                height: Math.max(timeline.appearance.dp(44), modelData.segmentMinutes * timeline.minuteHeight + (gesture === "resize" ? previewDelta * timeline.minuteHeight : 0))
                z: previewDelta !== 0 ? 3 : 1
                radius: timeline.appearance.dp(8)
                color: previewDelta !== 0 ? timeline.appearance.colors.selection : modelData.isEvent ? timeline.appearance.colors.event : timeline.appearance.colors.work
                border.color: modelData.warning ? timeline.appearance.colors.warning : timeline.appearance.colors.border
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
                    x: timeline.appearance.dp(7)
                    y: timeline.appearance.dp(4)
                    width: parent.width - timeline.appearance.dp(36)
                    Label {
                        width: parent.width
                        text: blockItem.modelData.time
                        font.pixelSize: timeline.appearance.dp(11)
                        font.bold: true
                        color: timeline.appearance.colors.accent
                        elide: Text.ElideRight
                    }
                    Label {
                        width: parent.width
                        textFormat: Text.PlainText
                        text: blockItem.modelData.title
                        font.pixelSize: timeline.appearance.dp(12)
                        color: timeline.appearance.colors.text
                        elide: Text.ElideRight
                    }
                    Label {
                        width: parent.width
                        visible: !!blockItem.modelData.warning && blockItem.height >= 60
                        text: blockItem.modelData.warning
                        elide: Text.ElideRight
                        color: timeline.appearance.colors.warning
                        font.pixelSize: timeline.appearance.dp(11)
                    }
                    Label {
                        visible: blockItem.previewDelta !== 0
                        text: (blockItem.previewDelta > 0 ? "+" : "") + blockItem.previewDelta + qsTr(" мин")
                        color: timeline.appearance.colors.accent
                        font.pixelSize: timeline.appearance.dp(11)
                    }
                }
                MouseArea {
                    id: mover
                    objectName: "timelineMove_" + blockItem.modelData.id
                    anchors.fill: parent
                    anchors.bottomMargin: timeline.appearance.dp(10)
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
                    width: timeline.appearance.dp(27)
                    height: timeline.appearance.dp(28)
                    text: "⋯"
                    enabled: !timeline.backend.busy
                    Accessible.name: qsTr("Изменить рабочий блок")
                    onClicked: timeline.editBlock(blockItem.modelData)
                }
                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: timeline.appearance.dp(9)
                    radius: timeline.appearance.dp(4)
                    color: blockItem.modelData.canResize ? (blockItem.modelData.isEvent ? timeline.appearance.colors.eventAccent : timeline.appearance.colors.work) : timeline.appearance.colors.track
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
