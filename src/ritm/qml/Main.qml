import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window

    objectName: "mainWindow"
    width: 1000
    height: 680
    minimumWidth: 640
    minimumHeight: 480
    visible: true
    title: qsTr("Ритм")
    color: "#f5f4ef"
    font.family: "Sans Serif"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 32
        spacing: 24

        RowLayout {
            Layout.fillWidth: true
            spacing: 14

            Rectangle {
                implicitWidth: 48
                implicitHeight: 48
                radius: 16
                color: "#274f42"

                Label {
                    anchors.centerIn: parent
                    text: qsTr("Р")
                    color: "#ffffff"
                    font.pixelSize: 27
                    font.weight: Font.DemiBold
                }
            }

            ColumnLayout {
                spacing: 2

                Label {
                    text: qsTr("Ритм")
                    color: "#203c33"
                    font.pixelSize: 25
                    font.weight: Font.DemiBold
                }

                Label {
                    text: qsTr("Задачи и время в своём темпе")
                    color: "#606e66"
                    font.pixelSize: 14
                }
            }

            Item {
                Layout.fillWidth: true
            }

            Label {
                text: qsTr("Ранняя версия")
                color: "#606e66"
                font.pixelSize: 13
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Rectangle {
                anchors.centerIn: parent
                width: Math.min(parent.width, 620)
                height: content.implicitHeight + 64
                radius: 24
                color: "#ffffff"
                border.color: "#e1e5dc"

                ColumnLayout {
                    id: content

                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 32
                    spacing: 16

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Место для ваших планов")
                        color: "#203c33"
                        font.pixelSize: 30
                        font.weight: Font.DemiBold
                        wrapMode: Text.WordWrap
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Здесь появятся ваши задачи, подзадачи и ближайшие дедлайны.")
                        color: "#606e66"
                        font.pixelSize: 17
                        wrapMode: Text.WordWrap
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Это первое окно «Ритма». Работа с задачами появится на следующем этапе.")
                        color: "#606e66"
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                    }

                    Button {
                        id: closeButton

                        objectName: "closeButton"
                        Layout.topMargin: 8
                        text: qsTr("Закрыть окно")
                        padding: 14
                        horizontalPadding: 22
                        hoverEnabled: true
                        onClicked: window.close()

                        contentItem: Label {
                            text: closeButton.text
                            color: "#ffffff"
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }

                        background: Rectangle {
                            radius: 12
                            color: closeButton.down ? "#16382c" : closeButton.hovered ? "#346653" : "#274f42"
                            border.width: closeButton.visualFocus ? 2 : 0
                            border.color: "#80b49a"
                        }
                    }
                }
            }
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Шаг за шагом — к свободному времени.")
            color: "#606e66"
            font.pixelSize: 13
            horizontalAlignment: Text.AlignHCenter
        }
    }
}
